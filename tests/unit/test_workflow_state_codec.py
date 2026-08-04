"""Workflow-state codec round-trip behaviour."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.workflow_state_codec import (
    STATE_SCHEMA_VERSION,
    WorkflowStateCodecError,
    dump_workflow_state,
    load_workflow_state,
)
from app.quotation_models import (
    ApprovalRecord,
    ApprovalStatus,
    CombinedDecision,
    CommercialRuleResult,
    CommercialValidationResult,
    ComparableQuotation,
    EmailOutput,
    PricingResult,
    TechnicalValidationResult,
    WorkflowStage,
    utc_now,
)
from app.workflow_state import initialize_workflow_state


def _populated_state():
    state = initialize_workflow_state(quotation_id="Q-CODEC-1")
    draft = state.draft
    draft.customer_name = "Synthetic Hospital"
    draft.region = "us"
    draft.currency = "EUR"
    draft.incoterm = "DDP"
    draft.delivery_location = "Springfield"
    draft.selected_product_ids = ["SYN-MAIN-1", "SYN-ACC-1"]
    draft.quantity = 3
    draft.requested_delivery_date = date(2026, 12, 1)
    draft.target_price = 95000.0
    draft.status = WorkflowStage.ANALYSED

    state.pricing_result = PricingResult(
        selected_product_ids=["SYN-MAIN-1"],
        currency="EUR",
        recommended_unit_price=100000.0,
        total_price=300000.0,
        confidence_score=0.82,
        confidence_label="high",
        assumptions=["synthetic"],
        internal_evidence=[
            ComparableQuotation(
                source_id="SYN-1",
                product_id="SYN-MAIN-1",
                quantity=1,
                net_price=91000.0,
                match_reasons=["exact model"],
            )
        ],
    )
    state.technical_validation = TechnicalValidationResult(
        status="pass", passed_checks=["compatibility"]
    )
    state.commercial_validation = CommercialValidationResult(
        status="pass_with_warnings",
        warnings=["Margin close to the floor."],
        rule_results=[
            CommercialRuleResult(
                rule_id="RULE-1",
                name="Minimum margin",
                status="warning",
                message="Within tolerance.",
            )
        ],
    )
    state.combined_decision = CombinedDecision(
        status="pass_with_warnings",
        summary="Proceed with review.",
        triggered_rule_ids=["RULE-1"],
        approval_required=True,
        recommended_next_action="Submit for approval.",
    )
    state.approval = ApprovalRecord(
        status=ApprovalStatus.PENDING_REVIEW,
        actor="Dana Approver",
        actor_role="Sales Manager",
        reminder_due_at=utc_now(),
    )
    state.internal_email = EmailOutput(
        email_type="internal_approval", subject="Internal", body="Body"
    )
    state.customer_email = EmailOutput(
        email_type="customer_quotation", subject="Customer", body="Body"
    )
    state.validation_stale = False
    state.current_stage = WorkflowStage.ANALYSED
    return state


def test_round_trip_is_lossless_for_a_fully_populated_state():
    original = _populated_state()

    document = dump_workflow_state(original)
    restored = load_workflow_state(document)

    assert dump_workflow_state(restored) == document


def test_round_trip_preserves_typed_values():
    restored = load_workflow_state(dump_workflow_state(_populated_state()))

    assert restored.current_stage is WorkflowStage.ANALYSED
    assert restored.draft.status is WorkflowStage.ANALYSED
    assert restored.approval.status is ApprovalStatus.PENDING_REVIEW
    assert restored.draft.requested_delivery_date == date(2026, 12, 1)
    assert isinstance(restored.approval.reminder_due_at.isoformat(), str)
    assert restored.validation_stale is False


def test_round_trip_preserves_nested_dataclasses():
    restored = load_workflow_state(dump_workflow_state(_populated_state()))

    evidence = restored.pricing_result.internal_evidence[0]
    assert isinstance(evidence, ComparableQuotation)
    assert evidence.net_price == 91000.0
    assert evidence.match_reasons == ["exact model"]

    rule = restored.commercial_validation.rule_results[0]
    assert isinstance(rule, CommercialRuleResult)
    assert rule.rule_id == "RULE-1"


def test_round_trip_preserves_audit_events():
    original = _populated_state()

    restored = load_workflow_state(dump_workflow_state(original))

    assert len(restored.audit_events) == len(original.audit_events)
    assert restored.audit_events[0].event_type == "draft_created"
    assert restored.audit_events[0].timestamp == original.audit_events[0].timestamp


def test_document_carries_a_schema_version():
    document = dump_workflow_state(_populated_state())

    assert document["schema_version"] == STATE_SCHEMA_VERSION


def test_unknown_keys_are_ignored_so_newer_documents_still_open():
    document = dump_workflow_state(_populated_state())
    document["a_field_from_the_future"] = {"anything": True}
    document["draft"]["another_future_field"] = 42

    restored = load_workflow_state(document)

    assert restored.draft.quotation_id == "Q-CODEC-1"


def test_a_document_without_a_draft_is_rejected():
    with pytest.raises(WorkflowStateCodecError):
        load_workflow_state({"current_stage": "draft"})


def test_a_non_mapping_document_is_rejected():
    with pytest.raises(WorkflowStateCodecError):
        load_workflow_state(["not", "a", "mapping"])


def test_an_unknown_workflow_stage_is_rejected():
    document = dump_workflow_state(_populated_state())
    document["current_stage"] = "teleported"

    with pytest.raises(WorkflowStateCodecError):
        load_workflow_state(document)


def test_deterministic_engines_accept_a_restored_state():
    """The restored object must still be usable by the existing state machine."""

    from app.approval_workflow import available_approval_actions

    restored = load_workflow_state(dump_workflow_state(_populated_state()))

    assert available_approval_actions(restored) == (
        "approve",
        "request_revision",
    )
