from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

from app.models import ValidationResult
from app.natural_language import QuoteRequest
from app.quotation_models import (
    LineItemCategory,
    QuotationLineItem,
    QuotationWorkflowState,
    WorkflowStage,
)
from app.recommender import QuoteRecommendation, RecommendationItem
from app.workflow_state import append_audit_event, reset_workflow_state


SCENARIO_SESSION_KEY = "demo_scenario_id"


@dataclass(frozen=True)
class DemoScenario:
    scenario_id: str
    name: str
    description: str
    customer_name: str
    price_profile: str


DEMO_SCENARIOS = (
    DemoScenario(
        scenario_id="straight_through",
        name="Scenario A — Straight-through approval",
        description=(
            "Exact product match, high pricing confidence, complete cost basis, "
            "and no user price override."
        ),
        customer_name="Example Medical Center",
        price_profile="recommended",
    ),
    DemoScenario(
        scenario_id="manager_review",
        name="Scenario B — Manager review",
        description=(
            "Uses the safe baseline with a material positive price deviation "
            "that requires documented review."
        ),
        customer_name="Sample Regional Clinic",
        price_profile="review_deviation",
    ),
    DemoScenario(
        scenario_id="blocked",
        name="Scenario C — Blocked quotation",
        description=(
            "Uses the safe baseline with a proposed price below the configured "
            "demo floor."
        ),
        customer_name="Demo Diagnostic Center",
        price_profile="below_floor",
    ),
)
SCENARIOS_BY_ID = {
    scenario.scenario_id: scenario for scenario in DEMO_SCENARIOS
}


@dataclass(frozen=True)
class MarginGateScenario:
    """A synthetic multi-line quotation with a predictable gate outcome."""

    scenario_id: str
    name: str
    description: str
    customer_name: str
    expected_status: str
    #: ``(description, category, quantity, unit_price, unit_cost)`` per line.
    lines: tuple[tuple[str, str, int, float, float | None], ...]


#: Synthetic demonstration quotations for the deterministic margin gate. The
#: threshold itself lives only in :mod:`app.commercial_policy`; these line
#: values are chosen so the documented outcome follows from it.
MARGIN_GATE_SCENARIOS = (
    MarginGateScenario(
        scenario_id="margin_pass",
        name="Scenario 1 — PASS (margin above threshold)",
        description=(
            "Synthetic two-line quotation with a complete cost basis and a "
            "gross margin above the active threshold."
        ),
        customer_name="Example Medical Center",
        expected_status="pass",
        lines=(
            ("Synthetic imaging system", "main_product", 1, 90000.0, 54000.0),
            ("Synthetic installation service", "installation", 1, 10000.0, 6000.0),
        ),
    ),
    MarginGateScenario(
        scenario_id="margin_review",
        name="Scenario 2 — REVIEW_REQUIRED (margin at the threshold)",
        description=(
            "Synthetic quotation whose gross margin lands exactly on the "
            "active threshold, so human override approval is required."
        ),
        customer_name="Sample Regional Clinic",
        expected_status="review_required",
        lines=(
            ("Synthetic imaging system", "main_product", 1, 80000.0, 52000.0),
            ("Synthetic installation service", "installation", 1, 20000.0, 13000.0),
        ),
    ),
    MarginGateScenario(
        scenario_id="margin_blocked",
        name="Scenario 3 — BLOCKED (no trusted cost basis)",
        description=(
            "Synthetic quotation containing a revenue line with no trusted "
            "cost basis, so no margin can be calculated and approval is "
            "impossible."
        ),
        customer_name="Demo Diagnostic Center",
        expected_status="blocked",
        lines=(
            ("Synthetic imaging system", "main_product", 1, 90000.0, 54000.0),
            ("Bespoke integration service without a costed bill of materials",
             "service", 1, 25000.0, None),
        ),
    ),
)
MARGIN_SCENARIOS_BY_ID = {
    scenario.scenario_id: scenario for scenario in MARGIN_GATE_SCENARIOS
}


def build_margin_gate_state(scenario_id: str) -> QuotationWorkflowState:
    """Build a ready-to-judge workflow state for a margin gate scenario."""

    scenario = MARGIN_SCENARIOS_BY_ID.get(scenario_id)
    if scenario is None:
        raise ValueError(f"Unknown margin gate scenario: {scenario_id}")
    state = reset_workflow_state({})
    draft = state.draft
    draft.customer_name = scenario.customer_name
    draft.region = "us"
    draft.product_query = "Synthetic margin gate demonstration quotation"
    draft.quantity = 1
    draft.currency = "USD"
    draft.incoterm = "DAP"
    draft.delivery_location = "Example City"
    draft.missing_fields = []
    draft.line_items = [
        QuotationLineItem(
            line_id=f"LI-DEMO-{index}",
            product_id="",
            description=description,
            category=LineItemCategory(category),
            quantity=quantity,
            unit_price=unit_price,
            currency="USD",
            source="demo_scenario",
            estimated_unit_cost=unit_cost,
            cost_source=(
                "synthetic_demo_cost_basis" if unit_cost is not None else ""
            ),
        )
        for index, (
            description,
            category,
            quantity,
            unit_price,
            unit_cost,
        ) in enumerate(scenario.lines, start=1)
    ]
    draft.status = WorkflowStage.READY_FOR_ANALYSIS
    state.current_stage = WorkflowStage.READY_FOR_ANALYSIS
    append_audit_event(
        state,
        "field_updated",
        actor="demo_user",
        before_state="not_ready",
        after_state="not_ready",
        changed_fields=["customer_name", "line_items"],
        reason=f"Loaded {scenario.name}",
    )
    return state


def load_demo_scenario(
    session_state: MutableMapping[str, Any],
    scenario_id: str,
) -> QuotationWorkflowState:
    scenario = SCENARIOS_BY_ID.get(scenario_id)
    if scenario is None:
        raise ValueError(f"Unknown demo scenario: {scenario_id}")
    state = reset_workflow_state(session_state)
    recommendation = build_demo_recommendation()
    main_product = recommendation.main_model
    if main_product is None:
        raise ValueError("Demo scenario recommendation has no main product.")
    draft = state.draft
    draft.customer_name = scenario.customer_name
    draft.region = "us"
    draft.product_query = recommendation.request.raw_text
    draft.selected_product_ids = [main_product.product_id]
    draft.quantity = 1
    draft.currency = "USD"
    draft.incoterm = "DAP"
    draft.delivery_location = "Example City"
    draft.missing_fields = []
    draft.status = WorkflowStage.READY_FOR_ANALYSIS
    state.current_stage = WorkflowStage.READY_FOR_ANALYSIS
    state.product_recommendation = recommendation
    session_state[SCENARIO_SESSION_KEY] = scenario_id
    append_audit_event(
        state,
        "field_updated",
        actor="demo_user",
        before_state="not_ready",
        after_state="not_ready",
        changed_fields=[
            "customer_name",
            "region",
            "product_query",
            "quantity",
            "currency",
            "incoterm",
            "delivery_location",
        ],
        reason=f"Loaded {scenario.name}",
    )
    append_audit_event(
        state,
        "product_selected",
        actor="demo_user",
        before_state="not_ready",
        after_state="not_ready",
        changed_fields=["selected_product_ids"],
        reason=f"Loaded {scenario.name}",
    )
    return state


def apply_demo_price_profile(
    state: QuotationWorkflowState,
    scenario_id: str | None,
) -> float | None:
    scenario = SCENARIOS_BY_ID.get(scenario_id or "")
    pricing = state.pricing_result
    if (
        scenario is None
        or pricing is None
        or pricing.recommended_unit_price is None
        or scenario.price_profile == "recommended"
    ):
        return None
    recommended = pricing.recommended_unit_price
    if scenario.price_profile == "review_deviation":
        proposed = round(recommended * 1.16, 2)
    else:
        floors = [
            value
            for value in (
                pricing.minimum_price_floor,
                pricing.gross_margin_floor,
            )
            if value is not None and value > 0
        ]
        proposed = round(
            (max(floors) * 0.9) if floors else (recommended * 0.5),
            2,
        )
    state.draft.proposed_unit_price = proposed
    append_audit_event(
        state,
        "price_edited",
        actor="demo_user",
        before_state=state.approval.status.value,
        after_state=state.approval.status.value,
        changed_fields=["proposed_unit_price"],
        reason=f"Applied {scenario.name} price profile",
    )
    return proposed


def build_demo_recommendation() -> QuoteRecommendation:
    request_text = (
        "Digital FMT X-ray room with motorized tube stand, table, "
        "Focus 43C detector and table grid"
    )
    request = QuoteRequest(
        raw_text=request_text,
        keywords=("digital", "fmt", "table", "grid"),
        product_ids=(),
        region="us",
        system_family="FMT",
        acquisition_type="digital",
        product_query=request_text,
    )
    main = _item(
        "DEMO-FMT-100",
        "X-Ray FMT System Digital Console PDU",
        "step_1a",
        "Exact configured demo system match.",
    )
    accessories = (
        _item(
            "DEMO-GEN-80",
            "CGN-80 Generator 80KW 3P 380-480 VAC",
            "step_2",
            "Configured generator.",
        ),
        _item(
            "DEMO-STAND-M",
            "FMT motorized tube stand",
            "step_3",
            "Supported motorized tube stand.",
        ),
        _item(
            "DEMO-DETECTOR-43",
            "Focus 43C detector",
            "step_6",
            "Detector selected for the table grid.",
        ),
        _item(
            "DEMO-TUBE-400",
            "X-Ray tube w/ E7254 & Ray-15_1/RAD-60",
            "step_8",
            "Generator-compatible tube specification.",
        ),
        _item(
            "DEMO-GRID-T",
            "Table grid 103L/I for Focus 43C",
            "step_10",
            "Supported detector and table-grid combination.",
        ),
        _item(
            "DEMO-TABLE-1",
            "FMT table QT-740",
            "step_11a",
            "Supported table configuration.",
        ),
    )
    return QuoteRecommendation(
        request=request,
        main_model=main,
        accessories=accessories,
        alternatives=(),
        validation=_valid_recommendation_result(),
        notices=("Synthetic non-customer demo scenario.",),
    )


def _item(
    product_id: str,
    description: str,
    step_id: str,
    reason: str,
) -> RecommendationItem:
    return RecommendationItem(
        product_id=product_id,
        short_description=description,
        quantity=1,
        step_id=step_id,
        option_group=None,
        reason=reason,
        source={},
    )


def _valid_recommendation_result() -> ValidationResult:
    return ValidationResult(status="valid", issues=())
