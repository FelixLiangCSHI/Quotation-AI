"""Phase 5 tests: multi-line pricing, margin gate and Agent 2 explanation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.agents import AgentProviderConfig, MockProvider
from app.agents.contracts import AgentProviderTimeout
from app.commercial_policy import (
    INTERNAL_MVP_PROVISIONAL_POLICY,
    CommercialPolicyRegistry,
    CommercialPolicyVersion,
    PolicyStatus,
    active_commercial_policy,
    policy_key,
)
from app.margin_gate import (
    RULE_COMM_MARGIN_001,
    RULE_COMM_MARGIN_002,
    RULE_DATA_001,
    RULE_DATA_002,
    RULE_DATA_003,
    RULE_TECH_001,
    STATUS_BLOCKED,
    STATUS_PASS,
    STATUS_REVIEW_REQUIRED,
    decision_display_margin,
    evaluate_commercial_decision,
)
from app.pricing_explanation import (
    AI_EXPLANATION_LABEL,
    deterministic_explanation,
    explain_pricing_decision,
)
from app.quotation_models import (
    LineItemCategory,
    QuotationDraft,
    QuotationLineItem,
    TechnicalValidationResult,
)
from app.quotation_pricing import (
    BLOCK_MISSING_COST,
    BLOCK_MIXED_CURRENCY,
    BLOCK_NEGATIVE_PRICE,
    BLOCK_ZERO_REVENUE,
    MARGIN_STATUS_AVAILABLE,
    MARGIN_STATUS_UNAVAILABLE,
    analyse_quotation_pricing,
    display_percent,
)


def _line(
    line_id: str,
    *,
    price,
    cost=None,
    quantity: int = 1,
    category: LineItemCategory = LineItemCategory.MAIN_PRODUCT,
    currency: str = "",
    product_id: str = "",
) -> QuotationLineItem:
    return QuotationLineItem(
        line_id=line_id,
        product_id=product_id or line_id,
        description=f"Synthetic {line_id}",
        category=category,
        quantity=quantity,
        unit_price=price,
        currency=currency,
        estimated_unit_cost=cost,
        cost_source="test_fixture" if cost is not None else "",
    )


def _draft(*lines: QuotationLineItem, currency: str = "USD") -> QuotationDraft:
    return QuotationDraft(
        quotation_id="Q-PHASE5-1",
        customer_name="Synthetic Hospital",
        region="usa",
        currency=currency,
        line_items=list(lines),
    )


def _analysis_for_margin(margin_percent: str):
    """Build a quotation whose exact margin is ``margin_percent``."""

    revenue = Decimal("1000")
    cost = revenue - revenue * Decimal(margin_percent) / Decimal("100")
    draft = _draft(_line("LI-1", price=str(revenue), cost=str(cost)))
    return analyse_quotation_pricing(draft)


def _decide(margin_percent: str, **kwargs):
    return evaluate_commercial_decision(
        _analysis_for_margin(margin_percent), **kwargs
    )


# --- margin boundary -------------------------------------------------------


@pytest.mark.parametrize(
    ("margin", "expected"),
    [
        ("40", STATUS_PASS),
        ("35.0001", STATUS_PASS),
        ("35.0", STATUS_REVIEW_REQUIRED),
        ("34.9999", STATUS_REVIEW_REQUIRED),
        ("20", STATUS_REVIEW_REQUIRED),
    ],
)
def test_margin_boundary(margin: str, expected: str) -> None:
    decision = _decide(margin)
    assert decision.status == expected


def test_exactly_at_threshold_is_not_a_pass() -> None:
    decision = _decide("35.0")
    assert decision.status == STATUS_REVIEW_REQUIRED
    assert decision.approval_required is True
    assert RULE_COMM_MARGIN_002 in decision.triggered_rule_ids


def test_pass_triggers_margin_001_and_does_not_auto_send() -> None:
    decision = _decide("40")
    assert RULE_COMM_MARGIN_001 in decision.triggered_rule_ids
    assert decision.approval_required is False
    assert "human confirmation" in decision.summary


# --- blocking --------------------------------------------------------------


def test_missing_material_cost_blocks() -> None:
    draft = _draft(_line("LI-1", price="1000", cost=None))
    analysis = analyse_quotation_pricing(draft)
    assert analysis.margin_status == MARGIN_STATUS_UNAVAILABLE
    assert BLOCK_MISSING_COST in analysis.blocking_reasons
    decision = evaluate_commercial_decision(analysis)
    assert decision.status == STATUS_BLOCKED
    assert RULE_DATA_002 in decision.triggered_rule_ids
    assert decision.evaluated_margin_percent is None


def test_missing_cost_is_not_treated_as_zero() -> None:
    draft = _draft(_line("LI-1", price="1000", cost=None))
    analysis = analyse_quotation_pricing(draft)
    assert analysis.line_analyses[0].total_cost is None
    assert analysis.gross_margin_percent is None


def test_invalid_quantity_blocks() -> None:
    with pytest.raises(ValueError):
        _line("LI-1", price="1000", cost="500", quantity=0)


def test_zero_quantity_forced_onto_draft_blocks() -> None:
    item = _line("LI-1", price="1000", cost="500")
    item.quantity = 0
    analysis = analyse_quotation_pricing(_draft(item))
    decision = evaluate_commercial_decision(analysis)
    assert decision.status == STATUS_BLOCKED
    assert RULE_DATA_001 in decision.triggered_rule_ids


def test_negative_price_blocks() -> None:
    analysis = analyse_quotation_pricing(
        _draft(_line("LI-1", price="-100", cost="10"))
    )
    assert BLOCK_NEGATIVE_PRICE in analysis.blocking_reasons
    assert evaluate_commercial_decision(analysis).status == STATUS_BLOCKED


def test_zero_quotation_revenue_blocks() -> None:
    analysis = analyse_quotation_pricing(
        _draft(_line("LI-1", price="0", cost="0"))
    )
    assert BLOCK_ZERO_REVENUE in analysis.blocking_reasons
    assert evaluate_commercial_decision(analysis).status == STATUS_BLOCKED


def test_technical_incompatibility_blocks() -> None:
    technical = TechnicalValidationResult(
        status="invalid", errors=["Detector is not supported by this generator."]
    )
    decision = _decide("60", technical=technical)
    assert decision.status == STATUS_BLOCKED
    assert RULE_TECH_001 in decision.triggered_rule_ids


def test_unnormalised_mixed_currency_blocks() -> None:
    analysis = analyse_quotation_pricing(
        _draft(
            _line("LI-1", price="1000", cost="500"),
            _line("LI-2", price="500", cost="100", currency="EUR"),
        )
    )
    assert BLOCK_MIXED_CURRENCY in analysis.blocking_reasons
    decision = evaluate_commercial_decision(analysis)
    assert decision.status == STATUS_BLOCKED
    assert RULE_DATA_003 in decision.triggered_rule_ids


def test_blocked_decision_forbids_approval() -> None:
    decision = evaluate_commercial_decision(
        analyse_quotation_pricing(_draft(_line("LI-1", price="1000")))
    )
    assert decision.approval_required is False
    assert "rerun" in decision.recommended_next_action


# --- calculation -----------------------------------------------------------


def test_quotation_margin_uses_totals_not_average_of_line_margins() -> None:
    draft = _draft(
        _line("LI-1", price="1000", cost="900"),  # 10% line margin
        _line("LI-2", price="100", cost="10"),  # 90% line margin
    )
    analysis = analyse_quotation_pricing(draft)
    # totals: revenue 1100, cost 910 -> 17.272727...%
    assert analysis.total_revenue == "1100.0000"
    assert analysis.total_cost == "910.0000"
    average_of_lines = Decimal("50")
    margin = Decimal(analysis.gross_margin_percent)
    assert margin != average_of_lines
    assert margin == (
        Decimal("190") / Decimal("1100") * Decimal("100")
    ).quantize(Decimal("0.000001"))


def test_multi_line_totals_include_quantities_and_bundles() -> None:
    draft = _draft(
        _line("LI-1", price="1000", cost="500", quantity=2),
        _line(
            "LI-2",
            price="100",
            cost="25",
            quantity=4,
            category=LineItemCategory.ACCESSORY,
        ),
    )
    analysis = analyse_quotation_pricing(draft)
    assert analysis.total_revenue == "2400.0000"
    assert analysis.total_cost == "1100.0000"
    bundles = {bundle.bundle_key: bundle for bundle in analysis.bundle_analyses}
    assert set(bundles) == {"main_product", "accessory"}
    assert bundles["accessory"].total_revenue == "400.0000"
    assert bundles["accessory"].margin_status == MARGIN_STATUS_AVAILABLE


def test_line_level_fields_are_stored() -> None:
    item = _line("LI-1", price="1000", cost="500", product_id="SYN-MAIN-1")
    item.list_price = 1200.0
    item.comparable_median_price = 1100.0
    item.comparable_count = 4
    item.recommended_unit_price = 1050.0
    item.approved_unit_price = 1000.0
    item.pricing_confidence = "High"
    item.pricing_data_version = "pricing-v1"
    analysis = analyse_quotation_pricing(_draft(item))
    line = analysis.line_analyses[0]
    assert line.product_id == "SYN-MAIN-1"
    assert line.line_item_type == "main_product"
    assert line.quantity == 1
    assert line.list_price == "1200.0000"
    assert line.comparable_median_price == "1100.0000"
    assert line.comparable_count == 4
    assert line.recommended_unit_price == "1050.0000"
    assert line.proposed_unit_price == "1000.0000"
    assert line.approved_unit_price == "1000.0000"
    assert line.estimated_unit_cost == "500.0000"
    assert line.total_cost == "500.0000"
    assert line.line_revenue == "1000.0000"
    assert line.gross_margin_amount == "500.0000"
    assert line.gross_margin_percent == "50.000000"
    assert line.pricing_confidence == "High"
    assert line.pricing_data_version == "pricing-v1"


def test_optional_zero_cost_component_is_permitted_by_policy() -> None:
    draft = _draft(
        _line("LI-1", price="1000", cost="500"),
        _line(
            "LI-2",
            price="50",
            cost=None,
            category=LineItemCategory.COMMERCIAL_ADDITION,
        ),
    )
    analysis = analyse_quotation_pricing(draft)
    assert analysis.margin_status == MARGIN_STATUS_AVAILABLE
    assert analysis.line_analyses[1].total_cost == "0.0000"


def test_service_line_without_cost_basis_blocks() -> None:
    draft = _draft(
        _line("LI-1", price="1000", cost="500"),
        _line(
            "LI-2",
            price="500",
            cost=None,
            category=LineItemCategory.SERVICE,
        ),
    )
    analysis = analyse_quotation_pricing(draft)
    assert BLOCK_MISSING_COST in analysis.blocking_reasons
    assert evaluate_commercial_decision(analysis).status == STATUS_BLOCKED


def test_decimal_precision_is_stable() -> None:
    first = _analysis_for_margin("33.333333")
    second = _analysis_for_margin("33.333333")
    assert first.gross_margin_percent == second.gross_margin_percent
    assert first.total_cost == second.total_cost


def test_display_rounding_does_not_alter_the_decision() -> None:
    decision = _decide("34.9999")
    # A rounded display value would read "35.00" and could look like a pass.
    assert decision_display_margin(decision) == "35.00"
    assert decision.status == STATUS_REVIEW_REQUIRED
    assert Decimal(decision.evaluated_margin_percent) < Decimal("35.0")


def test_full_internal_value_is_persisted_not_the_rounded_one() -> None:
    analysis = _analysis_for_margin("35.0001")
    assert analysis.gross_margin_percent == "35.000100"
    assert display_percent(analysis.gross_margin_percent) == "35.00"


# --- policy ----------------------------------------------------------------


def test_active_policy_is_the_provisional_mvp_policy() -> None:
    policy = active_commercial_policy()
    assert policy.policy_name == "Internal MVP Provisional Margin Policy"
    assert policy.pass_margin_threshold_percent == Decimal("35.0")
    assert policy.status is PolicyStatus.ACTIVE


def test_threshold_is_not_hardcoded_outside_the_policy_module() -> None:
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for path in (root / "app").rglob("*.py"):
        if path.name == "commercial_policy.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "35.0" in text or "Decimal(\"35\")" in text:
            offenders.append(path.name)
    assert offenders == []


def test_decision_records_its_policy_version_and_rule_trace() -> None:
    decision = _decide("40")
    assert decision.policy_version_id == policy_key(
        INTERNAL_MVP_PROVISIONAL_POLICY
    )
    assert decision.threshold_percent == "35.0"
    assert decision.comparison_operator == "greater_than"
    assert [entry.step for entry in decision.rule_trace] == sorted(
        entry.step for entry in decision.rule_trace
    )
    assert {entry.rule_id for entry in decision.rule_trace} >= {
        RULE_DATA_001,
        RULE_TECH_001,
        RULE_DATA_002,
        RULE_DATA_003,
        RULE_COMM_MARGIN_001,
    }


def _stricter_policy() -> CommercialPolicyVersion:
    return CommercialPolicyVersion(
        policy_id="POL-MARGIN-MVP-002",
        policy_name="Stricter Test Policy",
        version="1.0.0",
        effective_from=date(2024, 6, 1),
        pass_margin_threshold_percent=Decimal("45.0"),
        created_by="test",
    )


def test_registry_selects_the_latest_effective_active_policy() -> None:
    registry = CommercialPolicyRegistry(
        (INTERNAL_MVP_PROVISIONAL_POLICY, _stricter_policy())
    )
    assert registry.active_policy(on=date(2024, 3, 1)).policy_id == (
        "POL-MARGIN-MVP-001"
    )
    assert registry.active_policy(on=date(2024, 7, 1)).policy_id == (
        "POL-MARGIN-MVP-002"
    )


def test_changing_the_active_policy_does_not_mutate_old_decisions() -> None:
    original = _decide("40")
    assert original.status == STATUS_PASS

    registry = CommercialPolicyRegistry(
        (INTERNAL_MVP_PROVISIONAL_POLICY, _stricter_policy())
    )
    new_policy = registry.active_policy(on=date(2024, 7, 1))
    reevaluated = _decide("40", policy=new_policy)

    assert reevaluated.status == STATUS_REVIEW_REQUIRED
    assert original.status == STATUS_PASS
    assert original.policy_version_id == "POL-MARGIN-MVP-001@1.0.0"
    assert original.threshold_percent == "35.0"
    assert reevaluated.policy_version_id == "POL-MARGIN-MVP-002@1.0.0"


def test_historical_run_retains_its_policy_version() -> None:
    historical = _decide("40")
    registry = CommercialPolicyRegistry(
        (INTERNAL_MVP_PROVISIONAL_POLICY, _stricter_policy())
    )
    stored = registry.get(historical.policy_version_id)
    assert stored.pass_margin_threshold_percent == Decimal("35.0")


# --- Agent 2 ---------------------------------------------------------------


def _agent_config() -> AgentProviderConfig:
    return AgentProviderConfig(agent_name="agent2", provider="mock")


def test_deterministic_fallback_when_no_provider_is_configured() -> None:
    analysis = _analysis_for_margin("40")
    decision = evaluate_commercial_decision(analysis)
    explanation = explain_pricing_decision(analysis, decision)
    assert explanation.ai_generated is False
    assert explanation.fallback_used is True
    assert explanation.label == AI_EXPLANATION_LABEL
    assert "PASS" in explanation.explanation


def test_timeout_falls_back_to_the_deterministic_explanation() -> None:
    analysis = _analysis_for_margin("40")
    decision = evaluate_commercial_decision(analysis)
    provider = MockProvider(
        {"explain_quotation_margin": AgentProviderTimeout("timed out")}
    )
    explanation = explain_pricing_decision(
        analysis, decision, provider=provider, config=_agent_config()
    )
    assert explanation.fallback_used is True
    assert explanation.fallback_reason == "timeout"
    assert explanation.explanation == deterministic_explanation(
        analysis, decision
    ).explanation


def test_contradicting_the_decision_status_is_discarded() -> None:
    analysis = _analysis_for_margin("20")
    decision = evaluate_commercial_decision(analysis)
    baseline = deterministic_explanation(analysis, decision)
    provider = MockProvider(
        {
            "explain_quotation_margin": {
                "evidence_summary": baseline.summary,
                "analysis_explanation": (
                    "This quotation is a PASS and can be sent to the customer."
                ),
                "risks": [],
            }
        }
    )
    explanation = explain_pricing_decision(
        analysis, decision, provider=provider, config=_agent_config()
    )
    assert explanation.ai_generated is False
    assert explanation.fallback_reason in {"business_rule", "protected_field"}
    assert "REVIEW REQUIRED" in explanation.explanation


def test_dropping_a_protected_value_is_discarded() -> None:
    analysis = _analysis_for_margin("40")
    decision = evaluate_commercial_decision(analysis)
    provider = MockProvider(
        {
            "explain_quotation_margin": {
                "evidence_summary": "Looks fine.",
                "analysis_explanation": "Nothing to report.",
                "risks": [],
            }
        }
    )
    explanation = explain_pricing_decision(
        analysis, decision, provider=provider, config=_agent_config()
    )
    assert explanation.ai_generated is False
    assert explanation.fallback_reason == "protected_field"


def test_faithful_explanation_is_accepted_but_stays_labelled() -> None:
    analysis = _analysis_for_margin("40")
    decision = evaluate_commercial_decision(analysis)
    baseline = deterministic_explanation(analysis, decision)
    provider = MockProvider(
        {
            "explain_quotation_margin": {
                "evidence_summary": baseline.summary,
                "analysis_explanation": baseline.explanation,
                "risks": list(baseline.risks),
            }
        }
    )
    explanation = explain_pricing_decision(
        analysis, decision, provider=provider, config=_agent_config()
    )
    assert explanation.ai_generated is True
    assert explanation.label == AI_EXPLANATION_LABEL


def test_agent_2_cannot_change_the_calculated_result() -> None:
    analysis = _analysis_for_margin("20")
    decision = evaluate_commercial_decision(analysis)
    before = (
        analysis.total_revenue,
        analysis.total_cost,
        analysis.gross_margin_percent,
        decision.status,
        decision.threshold_percent,
    )
    provider = MockProvider(
        {
            "explain_quotation_margin": {
                "evidence_summary": "Revenue is 999999.99 USD.",
                "analysis_explanation": "Margin is 99.99%.",
                "risks": [],
            }
        }
    )
    explain_pricing_decision(
        analysis, decision, provider=provider, config=_agent_config()
    )
    assert (
        analysis.total_revenue,
        analysis.total_cost,
        analysis.gross_margin_percent,
        decision.status,
        decision.threshold_percent,
    ) == before
