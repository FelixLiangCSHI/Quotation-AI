"""Approval and audit persistence, and service rollback on failure."""

from __future__ import annotations

import pytest

from app.approval_workflow import (
    ApprovalWorkflowError,
    InvalidApprovalTransitionError,
)
from app.db import models
from app.quotation_models import (
    ApprovalStatus,
    CombinedDecision,
    CommercialValidationResult,
    PricingResult,
    TechnicalValidationResult,
    WorkflowStage,
)
from app.repositories.interfaces import QuotationVersionConflictError
from app.services.unit_of_work import UnitOfWork


def _make_approvable(state, *, decision_status: str = "pass") -> None:
    """Bring a workflow state to the point the approval gate allows review.

    Uses the real deterministic dataclasses so the existing approval
    restrictions are exercised rather than bypassed.
    """

    state.pricing_result = PricingResult(
        selected_product_ids=["SYN-MAIN-1"],
        currency="USD",
        recommended_unit_price=100000.0,
        total_price=100000.0,
        confidence_label="high",
    )
    state.technical_validation = TechnicalValidationResult(status="pass")
    state.commercial_validation = CommercialValidationResult(
        status=decision_status,
        approval_required=decision_status != "pass",
    )
    state.combined_decision = CombinedDecision(
        status=decision_status,
        summary="Synthetic decision for tests.",
        triggered_rule_ids=["RULE-SYN-1"],
        approval_required=decision_status != "pass",
        recommended_next_action="Submit for approval.",
    )
    state.validation_stale = False
    state.current_stage = WorkflowStage.ANALYSED


# -- approval persistence ---------------------------------------------


def test_approval_decision_is_persisted_and_survives_reload(service):
    loaded = service.create_quotation(quotation_id="Q-APP-0001")
    _make_approvable(loaded.state)
    loaded = service.save_state(loaded)

    loaded = service.submit_for_approval(
        loaded, approver_name="Dana Approver", approver_role="Sales Manager"
    )
    assert loaded.record.approval_status == ApprovalStatus.PENDING_REVIEW.value

    decided = service.decide_approval(
        loaded,
        action="approve",
        actor_role="Sales Manager",
        actor_name="Dana Approver",
        action_id="action-1",
    )

    assert decided.record.approval_status == ApprovalStatus.APPROVED.value

    reopened = service.load_quotation("Q-APP-0001")
    assert reopened.record.approval_status == ApprovalStatus.APPROVED.value
    assert reopened.record.status == WorkflowStage.APPROVED.value
    assert reopened.state.approval.status is ApprovalStatus.APPROVED
    assert reopened.state.approval.actor == "Dana Approver"
    assert reopened.state.approval.action_id == "action-1"


def test_approval_task_and_action_rows_are_written(service, session_factory):
    loaded = service.create_quotation(quotation_id="Q-APP-0002")
    _make_approvable(loaded.state)
    loaded = service.save_state(loaded)
    loaded = service.submit_for_approval(
        loaded, approver_name="Dana Approver", approver_role="Sales Manager"
    )
    service.decide_approval(
        loaded,
        action="approve",
        actor_role="Sales Manager",
        actor_name="Dana Approver",
        action_id="action-2",
    )

    with session_factory() as session:
        tasks = session.query(models.ApprovalTask).all()
        actions = session.query(models.ApprovalAction).all()

    assert len(tasks) == 1
    assert tasks[0].assigned_approver_name == "Dana Approver"
    assert tasks[0].status == ApprovalStatus.APPROVED.value
    assert tasks[0].decided_at is not None
    assert len(actions) == 1
    assert actions[0].action_id == "action-2"
    assert actions[0].from_status == ApprovalStatus.PENDING_REVIEW.value
    assert actions[0].to_status == ApprovalStatus.APPROVED.value


def test_override_requires_a_reason_and_persists_nothing_when_refused(
    service, session_factory
):
    loaded = service.create_quotation(quotation_id="Q-APP-0003")
    _make_approvable(loaded.state, decision_status="review_required")
    loaded = service.save_state(loaded)
    loaded = service.submit_for_approval(loaded, approver_role="Pricing Manager")

    with pytest.raises(ApprovalWorkflowError):
        service.decide_approval(
            loaded,
            action="approve_with_override",
            actor_role="Pricing Manager",
            actor_name="Pat Pricing",
            reason="",
            action_id="action-3",
        )

    with session_factory() as session:
        assert session.query(models.ApprovalAction).count() == 0
    assert (
        service.load_quotation("Q-APP-0003").record.approval_status
        == ApprovalStatus.PENDING_REVIEW.value
    )


def test_disallowed_action_for_decision_is_refused(service):
    loaded = service.create_quotation(quotation_id="Q-APP-0004")
    # A blocked decision permits only revision or rejection.
    _make_approvable(loaded.state, decision_status="blocked")
    loaded = service.save_state(loaded)
    loaded = service.submit_for_approval(loaded, approver_role="Sales Manager")

    with pytest.raises(InvalidApprovalTransitionError):
        service.decide_approval(
            loaded,
            action="approve",
            actor_role="Sales Manager",
            action_id="action-4",
        )


def test_invalid_approver_role_is_refused(service):
    loaded = service.create_quotation(quotation_id="Q-APP-0005")
    _make_approvable(loaded.state)
    loaded = service.save_state(loaded)
    loaded = service.submit_for_approval(loaded, approver_role="Sales Manager")

    with pytest.raises(ApprovalWorkflowError):
        service.decide_approval(
            loaded,
            action="approve",
            actor_role="Intern",
            action_id="action-5",
        )


def test_submitting_an_unready_quotation_is_refused(service):
    from app.services.quotation_service import QuotationServiceError

    loaded = service.create_quotation(quotation_id="Q-APP-0006")

    with pytest.raises(QuotationServiceError):
        service.submit_for_approval(loaded, approver_role="Sales Manager")


def test_rejection_is_persisted_with_its_reason(service):
    loaded = service.create_quotation(quotation_id="Q-APP-0007")
    _make_approvable(loaded.state, decision_status="blocked")
    loaded = service.save_state(loaded)
    loaded = service.submit_for_approval(loaded, approver_role="Sales Manager")

    service.decide_approval(
        loaded,
        action="reject",
        actor_role="Sales Manager",
        actor_name="Dana Approver",
        reason="Margin below the internal floor.",
        action_id="action-7",
    )

    reopened = service.load_quotation("Q-APP-0007")
    assert reopened.record.approval_status == ApprovalStatus.REJECTED.value
    assert reopened.record.status == WorkflowStage.REJECTED.value
    assert reopened.state.approval.reason == "Margin below the internal floor."


# -- audit persistence -------------------------------------------------


def test_creation_writes_an_audit_event(service):
    service.create_quotation(quotation_id="Q-AUD-0001")

    events = service.get_audit_trail("Q-AUD-0001")

    assert [event.event_type for event in events] == ["quotation_created"]
    assert events[0].quotation_reference == "Q-AUD-0001"
    assert events[0].occurred_at is not None


def test_every_material_event_is_persisted_in_order(service, three_line_items):
    loaded = service.create_quotation(quotation_id="Q-AUD-0002")
    _make_approvable(loaded.state)
    loaded = service.save_state(loaded, changed_fields=("pricing_result",))
    loaded = service.replace_line_items(loaded, three_line_items)
    loaded = service.submit_for_approval(loaded, approver_role="Sales Manager")
    loaded = service.decide_approval(
        loaded,
        action="approve",
        actor_role="Sales Manager",
        actor_name="Dana Approver",
        action_id="action-audit",
    )
    service.close_quotation(loaded)

    events = service.get_audit_trail("Q-AUD-0002")

    assert [event.event_type for event in events] == [
        "quotation_created",
        "quotation_updated",
        "line_items_updated",
        "approval_requested",
        "approval_approve",
        "quotation_closed",
    ]


def test_audit_event_records_state_transition_and_actor(service):
    loaded = service.create_quotation(quotation_id="Q-AUD-0003")
    _make_approvable(loaded.state)
    loaded = service.save_state(loaded)
    loaded = service.submit_for_approval(loaded, approver_role="Sales Manager")
    service.decide_approval(
        loaded,
        action="approve",
        actor_role="Sales Manager",
        actor_name="Dana Approver",
        action_id="action-audit-2",
    )

    approval_event = [
        event
        for event in service.get_audit_trail("Q-AUD-0003")
        if event.event_type == "approval_approve"
    ][0]

    assert approval_event.actor == "Dana Approver"
    assert approval_event.before_state == ApprovalStatus.PENDING_REVIEW.value
    assert approval_event.after_state == ApprovalStatus.APPROVED.value
    assert approval_event.triggered_rule_ids == ("RULE-SYN-1",)


def test_audit_events_are_attached_to_the_quotation_dto(service):
    service.create_quotation(quotation_id="Q-AUD-0004")

    record = service.load_quotation("Q-AUD-0004").record

    assert len(record.audit_events) == 1
    assert record.audit_events[0].event_type == "quotation_created"


def test_blank_event_type_or_actor_is_refused(service):
    service.create_quotation(quotation_id="Q-AUD-0005")

    with UnitOfWork(service._session_factory) as uow:
        with pytest.raises(ValueError):
            uow.audit_events.append(
                quotation_id="Q-AUD-0005", event_type="  ", actor="system"
            )
        with pytest.raises(ValueError):
            uow.audit_events.append(
                quotation_id="Q-AUD-0005", event_type="thing", actor="  "
            )


# -- transaction rollback ---------------------------------------------


def test_service_failure_rolls_back_state_and_audit(service):
    service.create_quotation(quotation_id="Q-TX-0001")

    def explode(state):
        state.draft.customer_name = "Never persisted"
        raise RuntimeError("deliberate failure inside the service call")

    with pytest.raises(RuntimeError, match="deliberate failure"):
        service.mutate("Q-TX-0001", explode)

    reopened = service.load_quotation("Q-TX-0001")
    assert reopened.record.customer_name == ""
    assert reopened.record.version == 1
    assert [event.event_type for event in service.get_audit_trail("Q-TX-0001")] == [
        "quotation_created"
    ]


def test_version_conflict_rolls_back_the_whole_unit_of_work(service):
    service.create_quotation(quotation_id="Q-TX-0002")
    stale = service.load_quotation("Q-TX-0002")
    service.save_state(service.load_quotation("Q-TX-0002"))

    with pytest.raises(QuotationVersionConflictError):
        service.save_state(stale)

    # The audit event that the failed call would have written is absent.
    events = service.get_audit_trail("Q-TX-0002")
    assert [event.event_type for event in events] == [
        "quotation_created",
        "quotation_updated",
    ]


def test_uncommitted_unit_of_work_persists_nothing(service, session_factory):
    with UnitOfWork(session_factory) as uow:
        uow.quotations.create(
            quotation_id="Q-TX-0003",
            state_document={"draft": {"quotation_id": "Q-TX-0003"}},
        )
        # Deliberately no commit.

    with session_factory() as session:
        assert session.query(models.Quotation).count() == 0


def test_exception_inside_unit_of_work_rolls_back(service, session_factory):
    with pytest.raises(RuntimeError):
        with UnitOfWork(session_factory) as uow:
            uow.quotations.create(
                quotation_id="Q-TX-0004",
                state_document={"draft": {"quotation_id": "Q-TX-0004"}},
            )
            raise RuntimeError("boom")

    with session_factory() as session:
        assert session.query(models.Quotation).count() == 0


def test_duplicate_approval_action_id_is_rejected_by_the_database(
    service, session_factory
):
    from app.repositories.interfaces import DuplicateApprovalActionError

    loaded = service.create_quotation(quotation_id="Q-TX-0005")
    _make_approvable(loaded.state)
    loaded = service.save_state(loaded)
    loaded = service.submit_for_approval(loaded, approver_role="Sales Manager")

    with UnitOfWork(session_factory) as uow:
        task_id = uow.approvals.get_open_task_id("Q-TX-0005")
        uow.approvals.record_action(
            task_id=task_id,
            action_id="replayed",
            action="approve",
            from_status="pending_review",
            to_status="approved",
        )
        uow.commit()

    with UnitOfWork(session_factory) as uow:
        with pytest.raises(DuplicateApprovalActionError):
            uow.approvals.record_action(
                task_id=task_id,
                action_id="replayed",
                action="approve",
                from_status="pending_review",
                to_status="approved",
            )
