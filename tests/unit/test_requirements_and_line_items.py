"""Phase 4 tests: requirement collection, recommendation and multi-line quotes."""

from __future__ import annotations

import json

import pytest

from app.agents import (
    Agent1RequirementAgent,
    AgentProviderConfig,
    MockProvider,
)
from app.agents.contracts import AgentProviderError
from app.conversation_agent import RequirementConversationAgent
from app.line_items import (
    LineItemError,
    add_line_item,
    build_recommendations,
    check_line_item_compatibility,
    line_items_by_category,
    quotation_total,
    remove_line_item,
    update_line_item,
)
from app.quotation_models import (
    ApprovalRecord,
    ApprovalStatus,
    CombinedDecision,
    EmailOutput,
    LineItemCategory,
    PricingResult,
    RecommendationStatus,
)
from app.requirement_fields import RequirementValidationError, validate_field
from app.requirement_intake import (
    RequirementCandidate,
    confirm_pending,
    merge_candidates,
    pending_confirmations,
)
from app.workflow_state import initialize_workflow_state
from tests.fixtures.catalog_fixtures import (
    synthetic_recommender,
    synthetic_rule_engine,
)


@pytest.fixture()
def engine():
    return synthetic_rule_engine()


@pytest.fixture()
def conversation_agent() -> RequirementConversationAgent:
    return RequirementConversationAgent(recommender=synthetic_recommender())


def _agent_with_response(payload: dict) -> Agent1RequirementAgent:
    config = AgentProviderConfig(agent_name="agent1", provider="mock")
    provider = MockProvider(responses={"extract_requirements": json.dumps(payload)})
    return Agent1RequirementAgent(config=config, provider=provider)


def _collected_state(conversation_agent):
    state = initialize_workflow_state()
    state.draft = conversation_agent.apply_structured_form(
        state.draft,
        {
            "customer_name": "Synthetic Hospital",
            "region": "china",
            "product_query": "synthetic imaging system",
            "quantity": 2,
            "currency": "USD",
            "incoterm": "DAP",
            "delivery_location": "Synthetic City",
        },
    ).draft
    state.current_stage = state.draft.status
    return state


# --- field validation ------------------------------------------------------


def test_validate_field_normalises_known_values():
    assert validate_field("currency", " rmb ") == "CNY"
    assert validate_field("incoterm", "dap") == "DAP"
    assert validate_field("region", " South ") == "south"
    assert validate_field("quantity", "3") == 3
    assert validate_field("requested_accessories", "grid, wall stand") == [
        "grid",
        "wall stand",
    ]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("currency", "BITCOIN"),
        ("incoterm", "FREE"),
        ("quantity", 0),
        ("quantity", "many"),
        ("customer_name", "   "),
    ],
)
def test_validate_field_rejects_invalid_values(field_name, value):
    with pytest.raises(RequirementValidationError):
        validate_field(field_name, value)


# --- multi-turn retention and correction -----------------------------------


def test_multi_turn_requirement_retention(conversation_agent):
    draft = initialize_workflow_state().draft
    result = conversation_agent.process_message(
        "Quotation for Synthetic Hospital in china", draft
    )
    assert result.updated_draft.customer_name

    result = conversation_agent.process_message(
        "We need 2 units of the synthetic imaging system",
        result.updated_draft,
    )
    assert result.updated_draft.quantity == 2
    # The earlier answers survive the later turns.
    assert result.updated_draft.region == "china"
    assert result.updated_draft.customer_name


def test_explicit_correction_replaces_a_confirmed_value(conversation_agent):
    state = _collected_state(conversation_agent)
    result = conversation_agent.process_message(
        "Actually change the quantity to 5",
        state.draft,
    )
    assert result.updated_draft.quantity == 5


def test_unmarked_repeat_does_not_silently_overwrite(conversation_agent):
    state = _collected_state(conversation_agent)
    result = conversation_agent.process_message("10 units", state.draft)
    assert result.updated_draft.quantity == 2
    assert result.notices


# --- structured form / conversation consistency ----------------------------


def test_structured_form_and_conversation_produce_the_same_model(
    conversation_agent,
):
    conversation_draft = initialize_workflow_state().draft
    for message in (
        "Quotation for Synthetic Hospital in china",
        "synthetic imaging system",
        "2",
        "USD",
        "DAP",
        "Synthetic City",
    ):
        conversation_draft = conversation_agent.process_message(
            message, conversation_draft
        ).updated_draft

    form_draft = conversation_agent.apply_structured_form(
        initialize_workflow_state().draft,
        {
            "customer_name": conversation_draft.customer_name,
            "region": "china",
            "product_query": conversation_draft.product_query,
            "quantity": 2,
            "currency": "USD",
            "incoterm": "DAP",
            "delivery_location": conversation_draft.delivery_location,
        },
    ).draft

    for field_name in (
        "customer_name",
        "region",
        "quantity",
        "currency",
        "incoterm",
        "delivery_location",
    ):
        assert getattr(form_draft, field_name) == getattr(
            conversation_draft, field_name
        )
    assert form_draft.missing_fields == conversation_draft.missing_fields


def test_structured_form_rejects_invalid_values(conversation_agent):
    outcome = conversation_agent.apply_structured_form(
        initialize_workflow_state().draft,
        {"currency": "BITCOIN", "quantity": -1, "region": "china"},
    )
    assert outcome.draft.currency == "USD"
    assert {item.field_name for item in outcome.rejected} == {
        "currency",
        "quantity",
    }


# --- Agent 1 integration ---------------------------------------------------


def test_agent1_candidates_are_merged_when_valid():
    draft = initialize_workflow_state().draft
    outcome = merge_candidates(
        draft,
        [
            RequirementCandidate("intended_use", "general radiography", 0.9, "agent1"),
            RequirementCandidate("currency", "EUR", 0.95, "agent1"),
        ],
    )
    assert outcome.draft.intended_use == "general radiography"
    assert outcome.draft.currency == "EUR"


def test_invalid_agent_extraction_cannot_corrupt_the_quotation():
    draft = initialize_workflow_state().draft
    outcome = merge_candidates(
        draft,
        [
            RequirementCandidate("currency", "MOONDOLLAR", 1.0, "agent1"),
            RequirementCandidate("quantity", "lots", 1.0, "agent1"),
            RequirementCandidate("proposed_unit_price", "1.00", 1.0, "agent1"),
        ],
    )
    assert outcome.draft.currency == "USD"
    assert outcome.draft.quantity == 1
    assert outcome.draft.proposed_unit_price is None
    assert len(outcome.rejected) == 3


def test_low_confidence_candidate_requires_explicit_confirmation():
    draft = initialize_workflow_state().draft
    outcome = merge_candidates(
        draft,
        [RequirementCandidate("delivery_location", "Synthetic City", 0.2, "agent1")],
    )
    assert outcome.draft.delivery_location == ""
    assert [item.field_name for item in pending_confirmations(outcome.draft)] == [
        "delivery_location"
    ]

    confirmed = confirm_pending(outcome.draft, "delivery_location", accept=True)
    assert confirmed.draft.delivery_location == "Synthetic City"
    assert confirmed.draft.pending_confirmations == []


def test_low_confidence_candidate_can_be_discarded():
    draft = initialize_workflow_state().draft
    outcome = merge_candidates(
        draft,
        [RequirementCandidate("intended_use", "guesswork", 0.1, "agent1")],
    )
    discarded = confirm_pending(outcome.draft, "intended_use", accept=False)
    assert discarded.draft.intended_use == ""
    assert discarded.draft.pending_confirmations == []


def test_agent1_is_optional_and_deterministic_flow_is_unchanged(
    conversation_agent,
):
    assert conversation_agent.requirement_agent is None
    result = conversation_agent.process_message(
        "Quotation for Synthetic Hospital in china", initialize_workflow_state().draft
    )
    assert result.agent_fallback_used is True
    assert result.updated_draft.region == "china"


def test_agent1_failure_falls_back_to_deterministic_output(conversation_agent):
    class FailingProvider:
        provider_name = "failing"

        def invoke(self, **_kwargs):
            raise AgentProviderError("provider unavailable")

        def health_check(self):  # pragma: no cover - not used
            raise NotImplementedError

    conversation_agent.requirement_agent = Agent1RequirementAgent(
        config=AgentProviderConfig(agent_name="agent1", provider="mock"),
        provider=FailingProvider(),
    )
    result = conversation_agent.process_message(
        "Quotation for Synthetic Hospital in china",
        initialize_workflow_state().draft,
    )
    assert result.agent_fallback_used is True
    assert result.updated_draft.region == "china"


def test_agent1_cannot_write_commercial_fields(conversation_agent):
    conversation_agent.requirement_agent = _agent_with_response(
        {
            "requirements": [
                {"field_name": "proposed_unit_price", "value": "1", "confidence": 1.0},
                {"field_name": "intended_use", "value": "chest imaging", "confidence": 1.0},
            ],
            "product_interpretation": "",
            "missing_questions": [],
            "recommendation_rationale": "",
        }
    )
    result = conversation_agent.process_message(
        "Quotation for Synthetic Hospital in china",
        initialize_workflow_state().draft,
    )
    assert result.updated_draft.proposed_unit_price is None
    assert result.updated_draft.intended_use == "chest imaging"


# --- multi-line quotation --------------------------------------------------


def test_multi_line_quotation_with_accessories_and_a_service(
    conversation_agent, engine
):
    state = _collected_state(conversation_agent)
    add_line_item(
        state,
        product_id="SYN-MAIN-1",
        description="Synthetic imaging system console",
        category=LineItemCategory.MAIN_PRODUCT,
        unit_price=100000.0,
        engine=engine,
    )
    add_line_item(
        state,
        product_id="SYN-ACC-1",
        category=LineItemCategory.ACCESSORY,
        quantity=2,
        unit_price=2500.0,
        engine=engine,
    )
    add_line_item(
        state,
        product_id="SYN-ACC-2",
        category=LineItemCategory.ACCESSORY,
        unit_price=1500.0,
        engine=engine,
    )
    add_line_item(
        state,
        description="Installation and commissioning",
        category=LineItemCategory.INSTALLATION,
        unit_price=7500.0,
        engine=engine,
    )

    assert len(state.draft.line_items) == 4
    assert len(line_items_by_category(state.draft, LineItemCategory.ACCESSORY)) == 2
    assert state.draft.selected_product_ids == ["SYN-MAIN-1"]
    assert quotation_total(state.draft) == 114000.0


def test_optional_lines_are_excluded_from_the_committed_total(
    conversation_agent, engine
):
    state = _collected_state(conversation_agent)
    add_line_item(
        state,
        product_id="SYN-MAIN-1",
        category=LineItemCategory.MAIN_PRODUCT,
        unit_price=100.0,
        engine=engine,
    )
    add_line_item(
        state,
        description="Extended warranty",
        category=LineItemCategory.WARRANTY,
        unit_price=50.0,
        is_optional=True,
        engine=engine,
    )
    assert quotation_total(state.draft) == 100.0
    assert quotation_total(state.draft, include_optional=True) == 150.0


def test_incompatible_item_is_rejected(conversation_agent, engine):
    state = _collected_state(conversation_agent)
    check = check_line_item_compatibility(
        state.draft,
        product_id="SYN-REGION-ONLY",
        category=LineItemCategory.ACCESSORY,
        engine=engine,
    )
    assert check.status is RecommendationStatus.INCOMPATIBLE
    with pytest.raises(LineItemError):
        add_line_item(
            state,
            product_id="SYN-REGION-ONLY",
            category=LineItemCategory.ACCESSORY,
            engine=engine,
        )
    assert state.draft.line_items == []


def test_unknown_product_is_rejected(conversation_agent, engine):
    state = _collected_state(conversation_agent)
    with pytest.raises(LineItemError):
        add_line_item(
            state,
            product_id="NOT-IN-CATALOGUE",
            category=LineItemCategory.ACCESSORY,
            engine=engine,
        )


def test_duplicate_product_line_is_rejected(conversation_agent, engine):
    state = _collected_state(conversation_agent)
    add_line_item(
        state,
        product_id="SYN-ACC-1",
        category=LineItemCategory.ACCESSORY,
        engine=engine,
    )
    with pytest.raises(LineItemError):
        add_line_item(
            state,
            product_id="SYN-ACC-1",
            category=LineItemCategory.ACCESSORY,
            engine=engine,
        )


def test_recommendation_statuses_are_distinguished(conversation_agent, engine):
    state = _collected_state(conversation_agent)
    recommendation = synthetic_recommender().recommend_from_text(
        "synthetic imaging system for china"
    )
    lines = build_recommendations(state.draft, recommendation, engine)
    statuses = {line.status for line in lines}
    assert RecommendationStatus.REQUIRED in statuses
    assert lines[0].category is LineItemCategory.MAIN_PRODUCT

    service_check = check_line_item_compatibility(
        state.draft,
        product_id="",
        category=LineItemCategory.SERVICE,
        engine=engine,
    )
    assert service_check.status is RecommendationStatus.NOT_EVALUATED

    state.draft.region = ""
    unknown_region = check_line_item_compatibility(
        state.draft,
        product_id="SYN-ACC-1",
        category=LineItemCategory.ACCESSORY,
        engine=engine,
    )
    assert unknown_region.status is RecommendationStatus.NOT_EVALUATED


# --- material-change invalidation ------------------------------------------


def _priced_and_approved(state):
    state.pricing_result = PricingResult(recommended_unit_price=100.0)
    state.combined_decision = CombinedDecision(
        status="pass",
        summary="ok",
        triggered_rule_ids=[],
        approval_required=False,
        recommended_next_action="issue",
    )
    state.validation_stale = False
    state.approval = ApprovalRecord(status=ApprovalStatus.APPROVED)
    state.customer_email = EmailOutput(
        email_type="customer", subject="s", body="b"
    )
    state.internal_email = EmailOutput(email_type="internal", subject="s", body="b")


def _assert_invalidated(state):
    assert state.pricing_result is None
    assert state.combined_decision is None
    assert state.validation_stale is True
    assert state.approval.status is ApprovalStatus.NOT_READY
    assert state.customer_email is None
    assert state.internal_email is None


def test_adding_a_line_invalidates_pricing_and_approval(
    conversation_agent, engine
):
    state = _collected_state(conversation_agent)
    _priced_and_approved(state)
    add_line_item(
        state,
        product_id="SYN-ACC-1",
        category=LineItemCategory.ACCESSORY,
        engine=engine,
    )
    _assert_invalidated(state)
    assert state.audit_events[-1].event_type == "line_item_added"


def test_quantity_edit_invalidates_pricing_and_approval(
    conversation_agent, engine
):
    state = _collected_state(conversation_agent)
    item = add_line_item(
        state,
        product_id="SYN-ACC-1",
        category=LineItemCategory.ACCESSORY,
        engine=engine,
    )
    _priced_and_approved(state)
    update_line_item(state, item.line_id, quantity=4)
    assert item.quantity == 4
    _assert_invalidated(state)


def test_no_op_edit_does_not_invalidate(conversation_agent, engine):
    state = _collected_state(conversation_agent)
    item = add_line_item(
        state,
        product_id="SYN-ACC-1",
        category=LineItemCategory.ACCESSORY,
        quantity=2,
        engine=engine,
    )
    _priced_and_approved(state)
    update_line_item(state, item.line_id, quantity=2)
    assert state.pricing_result is not None
    assert state.approval.status is ApprovalStatus.APPROVED


def test_removing_a_line_invalidates_pricing_and_approval(
    conversation_agent, engine
):
    state = _collected_state(conversation_agent)
    item = add_line_item(
        state,
        product_id="SYN-ACC-1",
        category=LineItemCategory.ACCESSORY,
        engine=engine,
    )
    _priced_and_approved(state)
    remove_line_item(state, item.line_id)
    assert state.draft.line_items == []
    _assert_invalidated(state)
