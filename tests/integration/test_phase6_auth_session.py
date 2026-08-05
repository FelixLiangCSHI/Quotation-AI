"""Phase 6: the UI session layer trusts only the stored session."""

from __future__ import annotations

import pytest

from app.auth.bootstrap import (
    ADMIN_PASSWORD_ENV,
    ADMIN_USERNAME_ENV,
    ensure_initial_administrator,
)
from app.auth.provider import AuthenticationError
from app.auth.roles import Permission, Role
from app.services.auth_session import current_user, sign_in, sign_out
from app.services.session_reference import (
    ACTIVE_USER_KEY,
    AUTH_TOKEN_KEY,
)
from tests.fixtures.phase6_helpers import PASSWORD, create_user

SECRET = PASSWORD


def test_an_unauthenticated_session_has_no_user(auth_provider):
    assert current_user({}, auth_provider) is None


def test_sign_in_stores_only_the_session_token(auth_provider):
    create_user(auth_provider, "manager1", Role.SALES_MANAGER)
    session_state: dict = {}

    user = sign_in(
        session_state,
        username="manager1",
        password=SECRET,
        provider=auth_provider,
    )

    assert session_state[AUTH_TOKEN_KEY] == user.session_token
    assert session_state[ACTIVE_USER_KEY] == user.user_id
    assert SECRET not in str(session_state)


def test_identity_and_permissions_are_resolved_from_the_database(auth_provider):
    create_user(auth_provider, "manager2", Role.SALES_MANAGER)
    session_state: dict = {}
    sign_in(
        session_state,
        username="manager2",
        password=SECRET,
        provider=auth_provider,
    )

    resolved = current_user(session_state, auth_provider)

    assert resolved is not None
    assert resolved.roles == (Role.SALES_MANAGER,)
    assert resolved.has_permission(Permission.APPROVE_PASS)


def test_a_forged_token_is_not_accepted(auth_provider):
    session_state = {AUTH_TOKEN_KEY: "not-a-real-token"}

    assert current_user(session_state, auth_provider) is None
    assert AUTH_TOKEN_KEY not in session_state


def test_a_forged_role_in_session_state_grants_nothing(auth_provider):
    create_user(auth_provider, "seller1", Role.SALES_USER)
    session_state: dict = {"roles": ["administrator"], "is_admin": True}
    sign_in(
        session_state,
        username="seller1",
        password=SECRET,
        provider=auth_provider,
    )

    resolved = current_user(session_state, auth_provider)

    assert resolved is not None
    assert resolved.roles == (Role.SALES_USER,)
    assert not resolved.has_permission(Permission.APPROVE_PASS)
    assert not resolved.has_permission(Permission.MANAGE_USERS)


def test_sign_out_revokes_the_stored_session(auth_provider):
    create_user(auth_provider, "manager3", Role.SALES_MANAGER)
    session_state: dict = {}
    sign_in(
        session_state,
        username="manager3",
        password=SECRET,
        provider=auth_provider,
    )
    token = session_state[AUTH_TOKEN_KEY]

    sign_out(session_state, auth_provider)

    assert AUTH_TOKEN_KEY not in session_state
    assert auth_provider.resolve_session(token) is None


def test_bad_credentials_are_refused(auth_provider):
    create_user(auth_provider, "manager4", Role.SALES_MANAGER)

    with pytest.raises(AuthenticationError):
        sign_in(
            {},
            username="manager4",
            password="wrong-password",
            provider=auth_provider,
        )


def test_bootstrap_creates_one_administrator_from_the_environment(
    auth_provider, monkeypatch
):
    monkeypatch.setenv(ADMIN_USERNAME_ENV, "root-admin")
    monkeypatch.setenv(ADMIN_PASSWORD_ENV, SECRET)

    result = ensure_initial_administrator(auth_provider)

    assert result.created is True
    assert result.generated_password is None
    principal = auth_provider.authenticate("root-admin", SECRET)
    assert principal.roles == (Role.ADMINISTRATOR,)


def test_bootstrap_is_a_no_op_once_a_user_exists(auth_provider, monkeypatch):
    monkeypatch.setenv(ADMIN_PASSWORD_ENV, SECRET)
    create_user(auth_provider, "seller2", Role.SALES_USER)

    result = ensure_initial_administrator(auth_provider)

    assert result.created is False


def test_bootstrap_generates_a_password_when_none_is_configured(
    auth_provider, monkeypatch
):
    monkeypatch.delenv(ADMIN_PASSWORD_ENV, raising=False)
    monkeypatch.setenv(ADMIN_USERNAME_ENV, "generated-admin")

    result = ensure_initial_administrator(auth_provider)

    assert result.created is True
    assert result.generated_password
    principal = auth_provider.authenticate(
        "generated-admin", result.generated_password
    )
    assert principal.roles == (Role.ADMINISTRATOR,)
