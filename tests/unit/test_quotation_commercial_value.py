"""The single commercial value resolver used by approval, documents and UI."""

from __future__ import annotations

from app.quotation_models import (
    PricingResult,
    QuotationPricingAnalysis,
    QuotationWorkflowState,
)
from app.workflow_state import initialize_workflow_state
from app.quotation_value import resolve_commercial_value
from tests.fixtures.phase7_helpers import add_line_items


def _state() -> QuotationWorkflowState:
    state = initialize_workflow_state(quotation_id="Q-VALUE-1")
    add_line_items(state)
    return state


def test_no_pricing_at_all_is_reported_as_unavailable():
    value = resolve_commercial_value(_state())

    assert value.is_available is False
    assert value.source == "unavailable"
    assert value.recommended_unit_price is None
    assert value.currency == "USD"


def test_a_single_product_quotation_uses_the_recommended_price():
    state = _state()
    state.pricing_result = PricingResult(
        currency="EUR", recommended_unit_price=1200.0, total_price=1200.0
    )

    value = resolve_commercial_value(state)

    assert value.source == "single_product"
    assert value.currency == "EUR"
    assert value.recommended_unit_price == 1200.0


def test_a_multi_line_quotation_uses_the_quotation_revenue():
    state = _state()
    state.draft.quantity = 2
    state.quotation_pricing = QuotationPricingAnalysis(
        currency="SGD", total_revenue="1000.00"
    )

    value = resolve_commercial_value(state)

    assert value.source == "multi_line"
    assert value.currency == "SGD"
    assert value.recommended_unit_price == 500.0
    assert value.total_price == 1000.0


def test_the_approved_price_wins_over_the_proposed_price():
    state = _state()
    state.pricing_result = PricingResult(
        currency="USD", recommended_unit_price=100.0
    )
    state.draft.proposed_unit_price = 90.0

    assert resolve_commercial_value(state).final_unit_price == 90.0

    state.approval.final_price = 95.0

    assert resolve_commercial_value(state).final_unit_price == 95.0
