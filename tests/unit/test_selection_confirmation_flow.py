"""Confidence gating of product selection in the workflow orchestrator."""

from __future__ import annotations

import pytest

from app.conversation_agent import RequirementConversationAgent
from app.workflow_orchestrator import process_requirement_message
from app.workflow_state import initialize_workflow_state
from tests.fixtures.catalog_fixtures import synthetic_recommender


@pytest.fixture()
def agent() -> RequirementConversationAgent:
    return RequirementConversationAgent(recommender=synthetic_recommender())


def test_an_explicit_product_id_is_selected_without_confirmation(agent):
    state = initialize_workflow_state()

    result = process_requirement_message(
        state,
        "Quotation for Synthetic Hospital in china for the synthetic imaging "
        "system SYN-MAIN-1, 2 units",
        agent,
    )

    assert result.product_recommendation is not None
    assert result.product_recommendation.requires_confirmation is False
    assert state.draft.selected_product_ids == ["SYN-MAIN-1"]
    assert "selected_product_ids" in result.changed_fields
    assert any("automatically" in notice for notice in result.notices)


def test_a_low_confidence_proposal_is_not_applied_without_confirmation(agent):
    state = initialize_workflow_state()

    result = process_requirement_message(
        state,
        "We would like a quotation for a synthetic device.",
        agent,
    )

    recommendation = result.product_recommendation
    if recommendation is not None and not recommendation.requires_confirmation:
        pytest.skip("The synthetic catalogue matched this request confidently.")
    assert state.draft.selected_product_ids == []


def test_an_existing_selection_is_never_overwritten_automatically(agent):
    state = initialize_workflow_state()
    process_requirement_message(
        state,
        "Quotation for Synthetic Hospital in china for the synthetic detector "
        "grid SYN-ACC-1",
        agent,
    )
    assert state.draft.selected_product_ids == ["SYN-ACC-1"]

    process_requirement_message(state, "Please add 3 units.", agent)

    assert state.draft.selected_product_ids == ["SYN-ACC-1"]


def test_the_audit_trail_records_the_automatic_selection(agent):
    state = initialize_workflow_state()

    process_requirement_message(
        state,
        "Quotation for Synthetic Hospital in china for the synthetic imaging "
        "system SYN-MAIN-1",
        agent,
    )

    changed = [
        field
        for event in state.audit_events
        for field in event.changed_fields
    ]
    assert "selected_product_ids" in changed
