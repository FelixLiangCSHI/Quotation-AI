"""Authentication gate and idle-session handling for the Streamlit shell.

The trusted identity always comes from the authentication provider, which
resolves the opaque session token against the database. This module adds only
the presentation-layer concerns Streamlit needs: an idle timeout, a single
place that answers "who is signed in", and a sign-out that clears every
session reference.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.auth.provider import AuthenticatedUser
from app.quotation_models import utc_now

#: Environment variable controlling the inactivity window, in minutes.
IDLE_TIMEOUT_ENV = "UI_IDLE_TIMEOUT_MINUTES"
#: Inactivity tolerated before the browser session is signed out.
DEFAULT_IDLE_TIMEOUT = timedelta(minutes=30)
#: Session key holding the last interaction timestamp.
LAST_SEEN_KEY = "ui_last_seen_at"
#: Session key holding the reason the previous session ended, shown once on
#: the login page.
TIMEOUT_NOTICE_KEY = "ui_session_timed_out"

__all__ = [
    "DEFAULT_IDLE_TIMEOUT",
    "IDLE_TIMEOUT_ENV",
    "LAST_SEEN_KEY",
    "TIMEOUT_NOTICE_KEY",
    "SessionStatus",
    "configured_idle_timeout",
    "evaluate_session",
    "is_expired",
    "touch",
]


def configured_idle_timeout(
    environment: "dict[str, str] | None" = None,
) -> timedelta:
    """Read the inactivity window, falling back to the default."""

    values = os.environ if environment is None else environment
    raw = (values.get(IDLE_TIMEOUT_ENV) or "").strip()
    if not raw:
        return DEFAULT_IDLE_TIMEOUT
    try:
        minutes = int(raw)
    except ValueError:
        return DEFAULT_IDLE_TIMEOUT
    if minutes <= 0:
        return DEFAULT_IDLE_TIMEOUT
    return timedelta(minutes=minutes)


def is_expired(
    last_seen: datetime | None,
    *,
    now: datetime | None = None,
    timeout: timedelta | None = None,
) -> bool:
    """Return whether the session has been idle for longer than ``timeout``."""

    if last_seen is None:
        return False
    moment = now or utc_now()
    window = timeout or configured_idle_timeout()
    return moment - last_seen > window


@dataclass(frozen=True)
class SessionStatus:
    """Outcome of the per-interaction session check."""

    user: AuthenticatedUser | None
    timed_out: bool = False

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None


def touch(session_state, *, now: datetime | None = None) -> None:
    """Record this interaction as the most recent activity."""

    session_state[LAST_SEEN_KEY] = now or utc_now()


def evaluate_session(
    session_state,
    *,
    resolve,
    end_session,
    now: datetime | None = None,
    timeout: timedelta | None = None,
) -> SessionStatus:
    """Resolve the signed-in principal, enforcing the inactivity window.

    ``resolve`` returns the authenticated principal for the stored token (or
    ``None``); ``end_session`` revokes it. Both are injected so this function
    stays free of Streamlit and of the provider implementation.
    """

    moment = now or utc_now()
    user = resolve(session_state)
    if user is None:
        session_state.pop(LAST_SEEN_KEY, None)
        return SessionStatus(user=None)

    last_seen = session_state.get(LAST_SEEN_KEY)
    if is_expired(last_seen, now=moment, timeout=timeout):
        end_session(session_state)
        session_state.pop(LAST_SEEN_KEY, None)
        session_state[TIMEOUT_NOTICE_KEY] = True
        return SessionStatus(user=None, timed_out=True)

    session_state[LAST_SEEN_KEY] = moment
    return SessionStatus(user=user)
