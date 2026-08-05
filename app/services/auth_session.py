"""Bridge between the UI session and the authentication provider.

Streamlit session state holds only an opaque token. The signed-in identity,
its roles and its permissions are resolved from the database on every
interaction, so a browser cannot claim a role it was not granted.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from app.auth.local_provider import LocalPasswordAuthenticationProvider
from app.auth.provider import AuthenticatedUser, AuthenticationError
from app.services.session_reference import (
    clear_authentication,
    read_auth_token,
    set_active_user,
    set_auth_token,
)

__all__ = [
    "current_user",
    "sign_in",
    "sign_out",
]


def current_user(
    session_state: MutableMapping[str, Any],
    provider: LocalPasswordAuthenticationProvider | None = None,
) -> AuthenticatedUser | None:
    """Resolve the signed-in principal, or ``None`` when unauthenticated."""

    token = read_auth_token(session_state)
    if not token:
        return None
    auth = provider or LocalPasswordAuthenticationProvider()
    user = auth.resolve_session(token)
    if user is None:
        clear_authentication(session_state)
        return None
    set_active_user(session_state, user.user_id)
    return user


def sign_in(
    session_state: MutableMapping[str, Any],
    *,
    username: str,
    password: str,
    provider: LocalPasswordAuthenticationProvider | None = None,
) -> AuthenticatedUser:
    """Authenticate and remember only the resulting session token."""

    auth = provider or LocalPasswordAuthenticationProvider()
    user = auth.authenticate(username, password)
    if not user.session_token:
        raise AuthenticationError("The provider did not issue a session.")
    set_auth_token(session_state, user.session_token)
    set_active_user(session_state, user.user_id)
    return user


def sign_out(
    session_state: MutableMapping[str, Any],
    provider: LocalPasswordAuthenticationProvider | None = None,
) -> None:
    token = read_auth_token(session_state)
    if token:
        auth = provider or LocalPasswordAuthenticationProvider()
        auth.end_session(token)
    clear_authentication(session_state)
