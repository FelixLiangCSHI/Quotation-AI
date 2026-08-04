"""Backward compatibility: the synthetic demo scenarios remain usable.

Phase 1 must not break the existing deterministic demo flow. These tests run
the real engines end to end and then prove the resulting state survives a
database round trip and can still be approved.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.data_loader import load_snapshot, synthetic_snapshot_path
from app.demo_scenarios import (
    DEMO_SCENARIOS,
    build_demo_recommendation,
    load_demo_scenario,
)
from app.domain.dto import LineItemDTO, LineItemType
from app.pricing_engine import PricingEngine
from app.quotation_models import ApprovalStatus, WorkflowStage
from app.rule_engine import QuotationRuleEngine
from app.workflow_orchestrator import analyse_workflow_pricing, validate_workflow


@pytest.fixture(scope="module")
def engines():
    snapshot = load_snapshot(synthetic_snapshot_path())
    return PricingEngine(), QuotationRuleEngine(snapshot)


def _run_demo(scenario_id: str, engines):
    """Drive a demo scenario through the existing deterministic engines."""

    pricing_engine, rule_engine = engines
    session_state: dict = {}
    state = load_demo_scenario(session_state, scenario_id)
    recommendation = build_demo_recommendation()
    analyse_workflow_pricing(state, pricing_engine, recommendation)
    decision = validate_workflow(state, recommendation, rule_engine)
    return state, decision


@pytest.mark.parametrize(
    "scenario_id", [scenario.scenario_id for scenario in DEMO_SCENARIOS]
)
def test_demo_scenarios_still_run_through_the_deterministic_engines(
    scenario_id, engines
):
    state, decision = _run_demo(scenario_id, engines)

    assert state.current_stage is WorkflowStage.ANALYSED
    assert state.pricing_result is not None
    assert state.pricing_result.recommended_unit_price is not None
    assert decision.status in {
        "pass",
        "pass_with_warnings",
        "review_required",
        "blocked",
    }


@pytest.mark.parametrize(
    "scenario_id", [scenario.scenario_id for scenario in DEMO_SCENARIOS]
)
def test_demo_scenario_state_survives_a_database_round_trip(
    scenario_id, engines, service
):
    state, decision = _run_demo(scenario_id, engines)
    quotation_id = state.draft.quotation_id

    service.create_quotation(state=state)
    reopened = service.load_quotation(quotation_id)

    assert reopened.record.customer_name == state.draft.customer_name
    assert reopened.state.current_stage is state.current_stage
    assert (
        reopened.state.pricing_result.recommended_unit_price
        == state.pricing_result.recommended_unit_price
    )
    assert reopened.state.combined_decision.status == decision.status
    assert len(reopened.state.audit_events) == len(state.audit_events)


def test_a_persisted_demo_quotation_can_still_be_approved(engines, service):
    state, decision = _run_demo("straight_through", engines)
    quotation_id = state.draft.quotation_id

    loaded = service.create_quotation(state=state)
    loaded = service.load_quotation(quotation_id)

    # The deterministic gate is unchanged, so only its permitted action works.
    assert "approve" in {
        *__import__(
            "app.approval_workflow", fromlist=["available_approval_actions"]
        ).available_approval_actions(loaded.state)
    }

    loaded = service.submit_for_approval(
        loaded, approver_name="Dana Approver", approver_role="Sales Manager"
    )
    service.decide_approval(
        loaded,
        action="approve",
        actor_role="Sales Manager",
        actor_name="Dana Approver",
        action_id="demo-approval",
    )

    reopened = service.load_quotation(quotation_id)
    assert reopened.record.approval_status == ApprovalStatus.APPROVED.value
    assert reopened.state.approval.status is ApprovalStatus.APPROVED


def test_a_persisted_demo_quotation_still_generates_a_pdf(engines, service):
    from app.document_generator import generate_quotation_pdf

    state, _ = _run_demo("straight_through", engines)
    quotation_id = state.draft.quotation_id
    loaded = service.create_quotation(state=state)
    loaded = service.submit_for_approval(
        service.load_quotation(quotation_id), approver_role="Sales Manager"
    )
    service.decide_approval(
        loaded,
        action="approve",
        actor_role="Sales Manager",
        actor_name="Dana Approver",
        action_id="demo-pdf",
    )

    # Regenerate from state recovered purely from the database.
    document = generate_quotation_pdf(service.load_quotation(quotation_id).state)

    assert document.mime_type == "application/pdf"
    assert len(document.bytes_data) > 1000


def test_a_demo_quotation_can_carry_three_line_items(engines, service):
    state, _ = _run_demo("straight_through", engines)
    loaded = service.create_quotation(state=state)

    service.replace_line_items(
        loaded,
        (
            LineItemDTO(
                position=0,
                item_type=LineItemType.MAIN_PRODUCT,
                product_id=state.draft.selected_product_ids[0],
                customer_description="Main system",
                proposed_unit_price=Decimal("100000.00"),
            ),
            LineItemDTO(
                position=1,
                item_type=LineItemType.WARRANTY,
                product_id="SYN-WAR-1",
                customer_description="Extended warranty",
                proposed_unit_price=Decimal("5000.00"),
            ),
            LineItemDTO(
                position=2,
                item_type=LineItemType.FREIGHT,
                product_id="SYN-FRT-1",
                customer_description="Freight and insurance",
                proposed_unit_price=Decimal("1250.00"),
            ),
        ),
    )

    record = service.load_quotation(state.draft.quotation_id).record
    assert len(record.line_items) == 3
    assert record.line_item_total == Decimal("106250.0000")


def test_workflow_state_module_no_longer_owns_trusted_state():
    """Session helpers must expose references only, not the workflow object."""

    from app.services.session_reference import (
        SESSION_REFERENCE_KEYS,
        read_session_reference,
        set_active_quotation,
    )

    session_state: dict = {}
    set_active_quotation(session_state, quotation_id="Q-REF-1", version=3)

    reference = read_session_reference(session_state)

    assert reference.quotation_id == "Q-REF-1"
    assert reference.quotation_version == 3
    # Only plain references are stored, never a workflow state object.
    assert set(session_state).issubset(set(SESSION_REFERENCE_KEYS))
    assert all(
        isinstance(value, (str, int, type(None)))
        for value in session_state.values()
    )
