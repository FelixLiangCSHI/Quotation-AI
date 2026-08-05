"""Local password authentication backed by the internal user table."""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from app.auth.passwords import hash_password, verify_password
from app.auth.provider import (
    DEFAULT_SESSION_LIFETIME,
    AuthenticatedUser,
    AuthenticationError,
    new_session_token,
)
from app.auth.roles import Role, UnknownRoleError, parse_roles
from app.quotation_models import utc_now
from app.services.unit_of_work import UnitOfWork


#: Failed sign-in attempts tolerated inside :data:`DEFAULT_LOCKOUT_WINDOW`
#: before an account is temporarily locked.
DEFAULT_MAX_FAILED_LOGINS = 5
DEFAULT_LOCKOUT_WINDOW = timedelta(minutes=15)


class AccountLockedError(AuthenticationError):
    """Raised when too many failed sign-in attempts were made."""


class LocalPasswordAuthenticationProvider:
    """Authenticates locally managed accounts and issues persistent sessions.

    Sessions are rows in ``user_sessions``, so an approver signing in from a
    separate browser session sees the same persistent workflow, and a session
    survives an application restart until it expires or is revoked.
    """

    provider_name = "local"

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        session_lifetime: timedelta = DEFAULT_SESSION_LIFETIME,
        max_failed_logins: int | None = None,
        lockout_window: timedelta = DEFAULT_LOCKOUT_WINDOW,
    ) -> None:
        self._session_factory = session_factory
        self._session_lifetime = session_lifetime
        self._max_failed_logins = (
            _configured_max_failed_logins()
            if max_failed_logins is None
            else max_failed_logins
        )
        self._lockout_window = lockout_window
        self._failures: dict[str, list[datetime]] = defaultdict(list)
        self._failure_lock = threading.Lock()

    # -- throttling ----------------------------------------------------

    def _recent_failures(self, key: str, now: datetime) -> int:
        cutoff = now - self._lockout_window
        attempts = [moment for moment in self._failures[key] if moment > cutoff]
        self._failures[key] = attempts
        return len(attempts)

    def _assert_not_locked(self, key: str, now: datetime) -> None:
        if self._max_failed_logins <= 0:
            return
        with self._failure_lock:
            if self._recent_failures(key, now) >= self._max_failed_logins:
                raise AccountLockedError(
                    "Too many failed sign-in attempts. Wait for the lockout "
                    "window to pass, or ask an administrator to reset the "
                    "account."
                )

    def _record_failure(self, key: str, now: datetime) -> None:
        with self._failure_lock:
            self._recent_failures(key, now)
            self._failures[key].append(now)

    def _clear_failures(self, key: str) -> None:
        with self._failure_lock:
            self._failures.pop(key, None)

    def _unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory)

    # -- account management -------------------------------------------

    def create_user(
        self,
        *,
        username: str,
        password: str,
        roles: tuple[Role | str, ...],
        display_name: str = "",
        email: str = "",
    ) -> AuthenticatedUser:
        """Create a local account with an explicitly assigned role set.

        ``roles`` must resolve to known :class:`Role` members, so a user can
        never be given a privileged role through free text.
        """

        resolved = parse_roles(roles)
        if not resolved:
            raise UnknownRoleError("At least one known role is required.")
        normalized = username.strip().casefold()
        if not normalized:
            raise AuthenticationError("A username is required.")

        with self._unit_of_work() as uow:
            if uow.users.get_by_username(normalized) is not None:
                raise AuthenticationError(
                    f"User {normalized!r} already exists."
                )
            created = uow.users.add(
                username=normalized,
                display_name=display_name or username.strip(),
                email=email,
                roles=tuple(role.value for role in resolved),
                password_hash=hash_password(password),
                auth_provider=self.provider_name,
            )
            uow.commit()

        return AuthenticatedUser(
            user_id=created.id,
            username=created.username,
            display_name=created.display_name,
            roles=resolved,
        )

    def set_roles(
        self, *, user_id: int, roles: tuple[Role | str, ...]
    ) -> AuthenticatedUser:
        resolved = parse_roles(roles)
        if not resolved:
            raise UnknownRoleError("At least one known role is required.")
        with self._unit_of_work() as uow:
            record = uow.users.set_roles(
                user_id=user_id,
                roles=tuple(role.value for role in resolved),
            )
            uow.commit()
        return AuthenticatedUser(
            user_id=record.id,
            username=record.username,
            display_name=record.display_name,
            roles=resolved,
        )

    # -- authentication ------------------------------------------------

    def authenticate(self, username: str, password: str) -> AuthenticatedUser:
        normalized = (username or "").strip().casefold()
        now = utc_now()
        expires_at = now + self._session_lifetime
        self._assert_not_locked(normalized, now)

        with self._unit_of_work() as uow:
            record = uow.users.get_credential(normalized)
            # The same message is used for an unknown user and a bad password
            # so the response does not disclose which accounts exist.
            if (
                record is None
                or not record.is_active
                or not verify_password(password or "", record.password_hash)
            ):
                self._record_failure(normalized, now)
                # The submitted password is never logged or stored.
                raise AuthenticationError("Invalid username or password.")

            self._clear_failures(normalized)
            roles = parse_roles(record.roles or ())
            token = new_session_token()
            uow.users.create_session(
                user_id=record.id,
                token=token,
                issued_at=now,
                expires_at=expires_at,
            )
            uow.users.record_login(user_id=record.id, moment=now)
            # The session token is a credential and is deliberately absent
            # from the audit record.
            uow.audit_events.append(
                quotation_id="",
                event_type="user_login",
                actor=record.username,
                actor_role=roles[0].value if roles else "",
                actor_user_id=record.id,
                after_state="authenticated",
                occurred_at=now,
                details={"auth_provider": self.provider_name},
            )
            principal = AuthenticatedUser(
                user_id=record.id,
                username=record.username,
                display_name=record.display_name,
                roles=roles,
                session_token=token,
                expires_at=expires_at,
            )
            uow.commit()
        return principal

    def resolve_session(self, token: str) -> AuthenticatedUser | None:
        if not token:
            return None
        with self._unit_of_work() as uow:
            record = uow.users.get_session(token)
            if record is None or record.revoked_at is not None:
                return None
            if record.expires_at <= utc_now():
                return None
            user = record.user
            if user is None or not user.is_active:
                return None
            return AuthenticatedUser(
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                roles=parse_roles(user.roles or ()),
                session_token=token,
                expires_at=record.expires_at,
            )

    def end_session(self, token: str) -> None:
        if not token:
            return
        with self._unit_of_work() as uow:
            uow.users.revoke_session(token, moment=utc_now())
            uow.commit()

    def has_any_user(self) -> bool:
        """Return whether any internal account exists yet."""

        with self._unit_of_work() as uow:
            return bool(uow.users.list_users(only_active=False))


def _configured_max_failed_logins(
    environment: "dict[str, str] | None" = None,
) -> int:
    values = os.environ if environment is None else environment
    raw = (values.get("AUTH_MAX_FAILED_LOGINS") or "").strip()
    if not raw:
        return DEFAULT_MAX_FAILED_LOGINS
    try:
        parsed = int(raw)
    except ValueError as error:
        raise ValueError("AUTH_MAX_FAILED_LOGINS must be an integer") from error
    return max(0, parsed)
