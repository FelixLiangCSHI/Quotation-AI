"""Authentication abstraction (Phase 6).

``AuthenticationProvider`` is the interface the service layer depends on. The
MVP ships :class:`LocalPasswordAuthenticationProvider`, which authenticates
locally managed internal accounts with securely hashed passwords and issues
persistent sessions.

An enterprise SSO provider can be added later by implementing the same
protocol; no enterprise SSO configuration exists yet, so none is implemented.
:class:`EnterpriseSsoAuthenticationProvider` documents that seam and refuses to
authenticate until it is configured.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from app.auth.passwords import hash_password, verify_password
from app.auth.roles import Permission, Role, parse_roles, permissions_for
from app.quotation_models import utc_now

#: How long an authenticated session remains valid without re-login.
DEFAULT_SESSION_LIFETIME = timedelta(hours=12)


class AuthenticationError(RuntimeError):
    """Raised when credentials are invalid or a session is not usable."""


class PermissionDeniedError(RuntimeError):
    """Raised when an authenticated principal lacks a required permission."""


@dataclass(frozen=True)
class AuthenticatedUser:
    """The authenticated principal handed to the service layer."""

    user_id: int
    username: str
    display_name: str
    roles: tuple[Role, ...]
    session_token: str = ""
    expires_at: datetime | None = None

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.roles)

    @property
    def primary_role(self) -> Role:
        return self.roles[0]

    def has_permission(self, permission: Permission) -> bool:
        return permission in self.permissions

    def require(self, permission: Permission) -> None:
        if not self.has_permission(permission):
            raise PermissionDeniedError(
                f"{self.username} is not permitted to {permission.value}."
            )


@runtime_checkable
class AuthenticationProvider(Protocol):
    """Contract shared by the local provider and any future SSO provider."""

    def authenticate(
        self, username: str, password: str
    ) -> AuthenticatedUser: ...

    def resolve_session(self, token: str) -> AuthenticatedUser | None: ...

    def end_session(self, token: str) -> None: ...


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


class EnterpriseSsoAuthenticationProvider:
    """Placeholder for enterprise SSO.

    Kept so the interface for a future integration is explicit. It refuses to
    authenticate because no SSO configuration is available in this phase.
    """

    def __init__(self, configuration: dict[str, str] | None = None) -> None:
        self.configuration = dict(configuration or {})

    @property
    def is_configured(self) -> bool:
        return bool(self.configuration)

    def authenticate(self, username: str, password: str) -> AuthenticatedUser:
        raise AuthenticationError(
            "Enterprise SSO is not configured for this deployment."
        )

    def resolve_session(self, token: str) -> AuthenticatedUser | None:
        return None

    def end_session(self, token: str) -> None:
        return None


__all__ = [
    "DEFAULT_SESSION_LIFETIME",
    "AuthenticatedUser",
    "AuthenticationError",
    "AuthenticationProvider",
    "EnterpriseSsoAuthenticationProvider",
    "PermissionDeniedError",
    "hash_password",
    "new_session_token",
    "parse_roles",
    "utc_now",
    "verify_password",
]
