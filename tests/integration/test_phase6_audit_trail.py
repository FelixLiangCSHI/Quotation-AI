"""Phase 6: audit persistence, references and role-restricted access."""

from __future__ import annotations

import pytest

from app.auth import PermissionDeniedError
from app.services.approval_service import apply_material_edit
from tests.fixtures.phase6_helpers import POLICY_VERSION_ID, make_decided_state

OVERRIDE_REASON = (
    "Volume commitment justifies it. I acknowledge the margin is equal to or "
    "below the configured policy threshold."
)


def _prepare(service, approval_service, people, quotation_id, status, margin):
    loaded = service.create_quotation(
        quotation_id=quotation_id, owner_user_id=people["sales"].user_id
    )
    service.record_event(
        quotation_id,
        "pricing_run_completed",
        actor=people["sales"].username,
        actor_role=people["sales"].primary_role.value,
        actor_user_id=people["sales"].user_id,
        details={"pricing_run_id": "PR-1"},
    )
    service.record_event(
        quotation_id,
        "technical_validation_completed",
        actor=people["sales"].username,
        actor_role=people["sales"].primary_role.value,
        details={"validation_run_id": "TV-1"},
    )
    service.record_event(
        quotation_id,
        "margin_calculated",
        actor="system",
        details={"gross_margin_percent": margin},
        policy_version_id=POLICY_VERSION_ID,
    )
    make_decided_state(loaded.state, status=status, margin=margin)
    service.record_event(
        quotation_id,
        "logical_decision_recorded",
        actor="system",
        after_state=status,
        policy_version_id=POLICY_VERSION_ID,
        triggered_rule_ids=tuple(loaded.state.combined_decision.triggered_rule_ids),
    )
    loaded = service.save_state(
        loaded,
        event_type="quotation_edited",
        actor=people["sales"].username,
        actor_role=people["sales"].primary_role.value,
        actor_user_id=people["sales"].user_id,
    )
    return approval_service.submit_for_approval(
        loaded,
        user=people["sales"],
        approver_user_id=people["manager"].user_id,
    )


def test_every_material_event_is_persisted(service, approval_service, people):
    task = _prepare(
        service, approval_service, people, "Q6-AUD-1", "pass", "42.0"
    )
    approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )
    service.record_event(
        "Q6-AUD-1",
        "customer_output_generated",
        actor=people["sales"].username,
        details={"document": "quotation.pdf"},
    )

    types = [event.event_type for event in service.get_audit_trail("Q6-AUD-1")]

    for expected in (
        "quotation_created",
        "pricing_run_completed",
        "technical_validation_completed",
        "margin_calculated",
        "logical_decision_recorded",
        "quotation_edited",
        "approval_submitted",
        "approver_assigned",
        "approval_approve",
        "customer_output_generated",
    ):
        assert expected in types, expected


def test_audit_record_stores_actor_role_version_policy_and_rules(
    service, approval_service, people
):
    task = _prepare(
        service, approval_service, people, "Q6-AUD-2", "review_required", "35.0"
    )
    approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="approve_with_override",
        reason=OVERRIDE_REASON,
        acknowledge_below_threshold=True,
        action_id="audited-request",
    )

    events = {
        event.event_type: event
        for event in service.get_audit_trail("Q6-AUD-2")
    }
    approval_event = events["approval_approve_with_override"]

    assert approval_event.actor == "mia.manager"
    assert approval_event.actor_role == "sales_manager"
    assert approval_event.quotation_version > 0
    assert approval_event.policy_version_id == POLICY_VERSION_ID
    assert approval_event.request_id == "audited-request"
    assert approval_event.triggered_rule_ids == ("COMM-MARGIN-002",)
    assert approval_event.reason == OVERRIDE_REASON

    override_event = events["override_justification_recorded"]
    assert override_event.details["evaluated_margin_percent"] == "35.0"
    assert override_event.details["original_decision"] == "review_required"


def test_revision_and_rejection_are_audited(
    service, approval_service, people
):
    task = _prepare(
        service, approval_service, people, "Q6-AUD-3", "blocked", None
    )
    approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="reject",
        reason="Cannot be supplied.",
    )

    events = [event for event in service.get_audit_trail("Q6-AUD-3")]
    rejection = [e for e in events if e.event_type == "approval_reject"][0]

    assert rejection.after_state == "rejected"
    assert rejection.reason == "Cannot be supplied."


def test_stale_task_cancellation_is_audited(service, approval_service, people):
    _prepare(service, approval_service, people, "Q6-AUD-4", "pass", "42.0")

    apply_material_edit(
        "Q6-AUD-4",
        user=people["sales"],
        edits={"quantity": 9},
        approval_service=approval_service,
        quotation_service=service,
    )

    types = [event.event_type for event in service.get_audit_trail("Q6-AUD-4")]
    assert "approval_task_cancelled_stale" in types
    assert "quotation_material_edit" in types


def test_audit_view_requires_the_audit_permission(
    service, approval_service, audit_service, people
):
    _prepare(service, approval_service, people, "Q6-AUD-5", "pass", "42.0")

    for role in ("sales", "manager", "pricing"):
        with pytest.raises(PermissionDeniedError):
            audit_service.list_for_quotation(people[role], "Q6-AUD-5")

    records = audit_service.list_for_quotation(people["admin"], "Q6-AUD-5")
    assert records


def test_audit_view_lists_recent_events_for_an_administrator(
    service, approval_service, audit_service, people
):
    _prepare(service, approval_service, people, "Q6-AUD-6", "pass", "42.0")

    recent = audit_service.list_recent(people["admin"], limit=5)

    assert 0 < len(recent) <= 5


def test_audit_view_never_returns_a_secret(
    service, approval_service, audit_service, people
):
    _prepare(service, approval_service, people, "Q6-AUD-7", "pass", "42.0")
    service.record_event(
        "Q6-AUD-7",
        "diagnostic_event",
        actor="system",
        details={"password": "hunter2", "token": "abc", "safe": "value"},
    )

    events = audit_service.list_for_quotation(people["admin"], "Q6-AUD-7")
    diagnostic = [e for e in events if e.event_type == "diagnostic_event"][0]

    assert diagnostic.details == {"safe": "value"}
