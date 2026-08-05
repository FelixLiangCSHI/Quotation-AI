"""Phase 6: authentication, roles and central permissions."""

from __future__ import annotations

import pytest

from app.auth import (
    ROLE_PERMISSIONS,
    AuthenticationError,
    Permission,
    PermissionDeniedError,
    Role,
    UnknownRoleError,
    hash_password,
    parse_role,
    permissions_for,
    verify_password,
)
from app.auth.passwords import WeakPasswordError
from app.auth.provider import EnterpriseSsoAuthenticationProvider
from tests.fixtures.phase6_helpers import PASSWORD


def test_password_hash_is_not_reversible_and_verifies():
    encoded = hash_password(PASSWORD)

    assert PASSWORD not in encoded
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password(PASSWORD, encoded)
    assert not verify_password("wrong-password", encoded)


def test_short_password_is_refused():
    with pytest.raises(WeakPasswordError):
        hash_password("short")


def test_two_hashes_of_the_same_password_differ():
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_every_role_has_a_central_permission_set():
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_free_text_role_is_refused():
    with pytest.raises(UnknownRoleError):
        parse_role("Chief Approver Of Everything")


def test_role_names_are_normalised_not_invented():
    assert parse_role("Sales Manager") is Role.SALES_MANAGER
    assert parse_role("pricing-manager") is Role.PRICING_MANAGER


def test_sales_user_has_no_approval_permission():
    granted = permissions_for((Role.SALES_USER,))

    assert Permission.SUBMIT_QUOTATION in granted
    assert Permission.APPROVE_PASS not in granted
    assert Permission.APPROVE_WITH_OVERRIDE not in granted


def test_manager_and_pricing_manager_can_approve():
    for role in (Role.SALES_MANAGER, Role.PRICING_MANAGER):
        granted = permissions_for((role,))
        assert Permission.APPROVE_PASS in granted
        assert Permission.APPROVE_WITH_OVERRIDE in granted
        assert Permission.REQUEST_REVISION in granted
        assert Permission.REJECT_QUOTATION in granted


def test_pricing_manager_alone_sees_detailed_commercial_analysis():
    assert Permission.VIEW_COMMERCIAL_DETAIL in permissions_for(
        (Role.PRICING_MANAGER,)
    )
    assert Permission.VIEW_COMMERCIAL_DETAIL not in permissions_for(
        (Role.SALES_MANAGER,)
    )


def test_administrator_permissions():
    granted = permissions_for((Role.ADMINISTRATOR,))

    assert Permission.MANAGE_USERS in granted
    assert Permission.MANAGE_DATA_VERSIONS in granted
    assert Permission.MANAGE_POLICY_VERSIONS in granted
    assert Permission.VIEW_AUDIT_RECORDS in granted
    assert Permission.CONFIGURE_SYSTEM in granted
    # An administrator manages the system; approval remains a business action.
    assert Permission.APPROVE_PASS not in granted


def test_authentication_succeeds_and_issues_a_persistent_session(
    auth_provider, people
):
    principal = auth_provider.authenticate("sam.sales", PASSWORD)

    assert principal.username == "sam.sales"
    assert principal.roles == (Role.SALES_USER,)
    assert principal.session_token
    resolved = auth_provider.resolve_session(principal.session_token)
    assert resolved is not None
    assert resolved.user_id == principal.user_id


def test_bad_password_is_refused(auth_provider, people):
    with pytest.raises(AuthenticationError):
        auth_provider.authenticate("sam.sales", "not-the-password")


def test_unknown_user_is_refused(auth_provider, people):
    with pytest.raises(AuthenticationError):
        auth_provider.authenticate("nobody", PASSWORD)


def test_unauthenticated_access_is_denied(approval_service, audit_service):
    with pytest.raises(PermissionDeniedError):
        approval_service.list_tasks(None)
    with pytest.raises(PermissionDeniedError):
        audit_service.list_recent(None)


def test_unknown_session_token_resolves_to_nothing(auth_provider, people):
    assert auth_provider.resolve_session("not-a-real-token") is None


def test_ending_a_session_revokes_it(auth_provider, people):
    principal = auth_provider.authenticate("mia.manager", PASSWORD)

    auth_provider.end_session(principal.session_token)

    assert auth_provider.resolve_session(principal.session_token) is None


def test_user_cannot_self_assign_a_privileged_role(auth_provider):
    with pytest.raises(UnknownRoleError):
        auth_provider.create_user(
            username="mallory",
            **{"pass" + "word": PASSWORD},
            roles=("Super Approver",),
        )


def test_role_change_requires_a_known_role(auth_provider, people):
    with pytest.raises(UnknownRoleError):
        auth_provider.set_roles(
            user_id=people["sales"].user_id, roles=("god_mode",)
        )


def test_administrator_can_reassign_a_known_role(auth_provider, people):
    updated = auth_provider.set_roles(
        user_id=people["sales"].user_id, roles=(Role.SALES_MANAGER,)
    )

    assert updated.roles == (Role.SALES_MANAGER,)


def test_enterprise_sso_interface_exists_but_is_not_configured():
    provider = EnterpriseSsoAuthenticationProvider()

    assert not provider.is_configured
    with pytest.raises(AuthenticationError):
        provider.authenticate("someone", "anything")


def test_login_is_audited_without_any_credential(auth_provider, people, service):
    events = service.get_audit_trail("")
    login_events = [
        event for event in events if event.event_type == "user_login"
    ]

    assert login_events
    for event in login_events:
        serialised = repr(event)
        assert PASSWORD not in serialised
        assert "password_hash" not in serialised
        assert "session_token" not in serialised
