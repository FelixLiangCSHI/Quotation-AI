"""Phase 5 workflow integration: analysis, judgement, staleness, persistence."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.workflow_state_codec import (
    dump_workflow_state,
    load_workflow_state,
)
from app.line_items import add_line_item, update_line_item
from app.margin_gate import STATUS_PASS, STATUS_REVIEW_REQUIRED
from app.quotation_models import (
    ApprovalStatus,
    LineItemCategory,
    QuotationLineItem,
)
from app.workflow_orchestrator import (
    WorkflowOrchestrationError,
    analyse_quotation_lines,
    judge_quotation,
)
from app.workflow_state import initialize_workflow_state
from tests.fixtures.catalog_fixtures import synthetic_rule_engine


@pytest.fixture()
def engine():
    return synthetic_rule_engine()


def _state_with_lines():
    state = initialize_workflow_state(quotation_id="Q-PHASE5-WF")
    state.draft.customer_name = "Synthetic Hospital"
    state.draft.region = "usa"
    state.draft.currency = "USD"
    state.draft.line_items = [
        QuotationLineItem(
            line_id="LI-1",
            product_id="SYN-MAIN-1",
            description="Synthetic imaging system",
            category=LineItemCategory.MAIN_PRODUCT,
            quantity=1,
            unit_price=100000.0,
            estimated_unit_cost=50000.0,
            cost_source="test_fixture",
        ),
        QuotationLineItem(
            line_id="LI-2",
            product_id="SYN-ACC-1",
            description="Synthetic detector grid",
            category=LineItemCategory.ACCESSORY,
            quantity=2,
            unit_price=5000.0,
            estimated_unit_cost=3000.0,
            cost_source="test_fixture",
        ),
    ]
    return state


def test_pass_case_runs_end_to_end() -> None:
    state = _state_with_lines()
    analysis = analyse_quotation_lines(state)
    decision = judge_quotation(state)

    assert analysis.total_revenue == "110000.0000"
    assert analysis.total_cost == "56000.0000"
    assert decision.status == STATUS_PASS
    assert decision.pricing_run_id == analysis.pricing_run_id
    assert decision.policy_version_id == "POL-MARGIN-MVP-001@1.0.0"
    assert state.pricing_explanation is not None
    assert state.pricing_explanation.ai_generated is False
    assert state.validation_stale is False


def test_review_required_creates_a_human_approval_task() -> None:
    state = _state_with_lines()
    state.draft.line_items[0].estimated_unit_cost = 80000.0
    analyse_quotation_lines(state)
    decision = judge_quotation(state)

    assert decision.status == STATUS_REVIEW_REQUIRED
    assert decision.approval_required is True
    assert state.approval.status is ApprovalStatus.PENDING_REVIEW


def test_material_edit_marks_pricing_and_judgement_stale(engine) -> None:
    state = _state_with_lines()
    analyse_quotation_lines(state)
    judge_quotation(state)
    assert state.quotation_pricing is not None

    update_line_item(state, "LI-2", quantity=5)

    assert state.quotation_pricing is None
    assert state.combined_decision is None
    assert state.pricing_explanation is None
    assert state.validation_stale is True


def test_adding_a_line_item_marks_the_judgement_stale(engine) -> None:
    state = _state_with_lines()
    analyse_quotation_lines(state)
    judge_quotation(state)

    add_line_item(
        state,
        product_id="SYN-ACC-2",
        description="Synthetic wall stand",
        category=LineItemCategory.ACCESSORY,
        quantity=1,
        unit_price=1000.0,
        engine=engine,
    )

    assert state.quotation_pricing is None
    assert state.combined_decision is None


def test_judgement_requires_a_pricing_run() -> None:
    state = _state_with_lines()
    with pytest.raises(WorkflowOrchestrationError):
        judge_quotation(state)


def test_pricing_and_decision_survive_a_persistence_round_trip() -> None:
    state = _state_with_lines()
    analyse_quotation_lines(state)
    decision = judge_quotation(state)

    restored = load_workflow_state(dump_workflow_state(state))

    assert restored.quotation_pricing is not None
    assert restored.quotation_pricing.total_revenue == "110000.0000"
    assert len(restored.quotation_pricing.line_analyses) == 2
    assert restored.combined_decision.status == decision.status
    assert (
        restored.combined_decision.policy_version_id
        == decision.policy_version_id
    )
    assert restored.combined_decision.rule_trace
    assert restored.pricing_explanation.ai_generated is False


def test_audit_trail_records_the_policy_version_and_margin() -> None:
    state = _state_with_lines()
    analyse_quotation_lines(state)
    judge_quotation(state)

    event = next(
        item
        for item in state.audit_events
        if item.event_type == "commercial_decision_completed"
    )
    assert event.details["policy_version_id"] == "POL-MARGIN-MVP-001@1.0.0"
    assert Decimal(event.details["evaluated_margin_percent"]) > Decimal("35.0")
    assert event.details["threshold_percent"] == "35.0"
