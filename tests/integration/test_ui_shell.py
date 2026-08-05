"""Role-based navigation, session timeout and dashboard projections.

These tests cover the presentation layer only. Nothing here exercises or
changes a business rule: permissions still come from ``app.auth.roles`` and
every figure comes from the existing services.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.auth.roles import Permission, Role
from app.quotation_models import utc_now
from app.ui.dashboard_data import build_dashboard
from app.ui.navigation import (
    WORKSPACES,
    default_page_key,
    landing_headline,
    pages_for,
)
from app.ui.session_guard import (
    LAST_SEEN_KEY,
    TIMEOUT_NOTICE_KEY,
    configured_idle_timeout,
    evaluate_session,
    is_expired,
    touch,
)


def _keys(user) -> tuple[str, ...]:
    return tuple(entry.key for entry in pages_for(user))


# -- navigation --------------------------------------------------------


def test_a_sales_user_sees_only_sales_workspaces(people):
    keys = _keys(people["sales"])

    assert "create_quotation" in keys
    assert "my_quotations" in keys
    assert "approval_center" not in keys
    assert "pricing_data" not in keys
    assert "users" not in keys


def test_an_approver_sees_approval_workspaces_but_cannot_create(people):
    keys = _keys(people["manager"])

    assert "approval_center" in keys
    assert "approval_history" in keys
    assert "create_quotation" not in keys


def test_a_pricing_manager_sees_policy_management(people):
    keys = _keys(people["pricing"])

    assert "policy" in keys
    assert "approval_center" in keys
    assert "users" not in keys


def test_an_administrator_sees_administration_workspaces(people):
    keys = _keys(people["admin"])

    assert {"users", "system", "pricing_data", "audit"} <= set(keys)
    assert "create_quotation" not in keys


def test_every_role_starts_on_a_page_it_may_open(people):
    for user in people.values():
        assert default_page_key(user) in _keys(user)


def test_two_roles_get_different_starting_headlines(people):
    assert landing_headline(people["sales"]) != landing_headline(
        people["manager"]
    )


def test_no_workspace_is_visible_without_its_permission(people):
    for entry in WORKSPACES:
        if not entry.permissions:
            continue
        for user in people.values():
            visible = entry.is_visible_to(user)
            permitted = any(
                user.has_permission(item) for item in entry.permissions
            )
            assert visible is permitted


# -- session timeout ---------------------------------------------------


def test_a_fresh_session_is_not_expired():
    assert is_expired(utc_now()) is False


def test_an_idle_session_is_expired():
    stale = utc_now() - timedelta(hours=2)

    assert is_expired(stale, timeout=timedelta(minutes=30)) is True


def test_the_idle_window_is_configurable():
    assert configured_idle_timeout({"UI_IDLE_TIMEOUT_MINUTES": "5"}) == (
        timedelta(minutes=5)
    )


@pytest.mark.parametrize("raw", ["", "not-a-number", "0", "-3"])
def test_an_invalid_idle_window_falls_back_to_the_default(raw):
    assert configured_idle_timeout({"UI_IDLE_TIMEOUT_MINUTES": raw}) == (
        timedelta(minutes=30)
    )


def test_an_idle_session_is_signed_out_and_flagged(people):
    ended: list[str] = []
    state = {LAST_SEEN_KEY: utc_now() - timedelta(hours=3)}

    status = evaluate_session(
        state,
        resolve=lambda _state: people["sales"],
        end_session=lambda _state: ended.append("revoked"),
        timeout=timedelta(minutes=30),
    )

    assert status.is_authenticated is False
    assert status.timed_out is True
    assert ended == ["revoked"]
    assert state[TIMEOUT_NOTICE_KEY] is True
    assert LAST_SEEN_KEY not in state


def test_an_active_session_is_kept_and_refreshed(people):
    state: dict = {}
    touch(state)
    first_seen = state[LAST_SEEN_KEY]

    status = evaluate_session(
        state,
        resolve=lambda _state: people["manager"],
        end_session=lambda _state: pytest.fail("must not sign out"),
        timeout=timedelta(minutes=30),
    )

    assert status.is_authenticated is True
    assert state[LAST_SEEN_KEY] >= first_seen


def test_an_unauthenticated_session_is_not_reported_as_timed_out():
    state = {LAST_SEEN_KEY: utc_now() - timedelta(days=1)}

    status = evaluate_session(
        state,
        resolve=lambda _state: None,
        end_session=lambda _state: pytest.fail("must not sign out"),
    )

    assert status.is_authenticated is False
    assert status.timed_out is False
    assert LAST_SEEN_KEY not in state


# -- dashboard ---------------------------------------------------------


def _dashboard(user, session_factory, service):
    from app.services.approval_service import ApprovalService
    from app.services.audit_view import AuditViewService

    return build_dashboard(
        user,
        quotations=service,
        approvals=ApprovalService(session_factory, service),
        audit=AuditViewService(session_factory),
    )


def test_a_sales_dashboard_counts_only_its_own_quotations(
    people, session_factory, service
):
    sales = people["sales"]
    service.create_quotation(actor=sales.username, owner_user_id=sales.user_id)
    service.create_quotation(actor="someone.else", owner_user_id=None)

    data = _dashboard(sales, session_factory, service)

    assert data.active_quotation_count == 1
    assert data.pending_task_count == 0


def test_an_empty_dashboard_is_not_an_error(people, session_factory, service):
    data = _dashboard(people["manager"], session_factory, service)

    assert data.active_quotations == ()
    assert data.pending_tasks == ()


def test_a_role_without_quotation_access_gets_a_notice(
    people, session_factory, service
):
    data = _dashboard(people["admin"], session_factory, service)

    assert people["admin"].has_permission(Permission.VIEW_OWN_QUOTATIONS) is (
        False
    )
    assert data.my_quotations == ()
    assert data.notices


def test_the_administrator_dashboard_reads_audit_records(
    people, session_factory, service
):
    service.create_quotation(actor="sam.sales")

    data = _dashboard(people["admin"], session_factory, service)

    assert data.recent_events


def test_roles_are_a_closed_set_for_navigation():
    assert set(Role) == {
        Role.SALES_USER,
        Role.SALES_MANAGER,
        Role.PRICING_MANAGER,
        Role.ADMINISTRATOR,
    }
