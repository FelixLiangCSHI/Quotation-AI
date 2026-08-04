from __future__ import annotations

from decimal import Decimal

from app.config import (
    DEMO_AUTO_DISCOUNT_LIMIT_PERCENT,
    DEMO_MANAGER_DISCOUNT_LIMIT_PERCENT,
    DEMO_MIN_GROSS_MARGIN_PERCENT,
    DEMO_PRICE_DEVIATION_BLOCK_PERCENT,
    DEMO_PRICE_DEVIATION_REVIEW_PERCENT,
    DEMO_REVIEW_MARGIN_PERCENT,
)
from app.quotation_models import (
    CombinedDecision,
    CommercialRuleResult,
    CommercialValidationResult,
    PricingResult,
    QuotationDraft,
    TechnicalValidationResult,
)


COMMERCIAL_RULE_NAMES = {
    "CV-001": "Required fields complete",
    "CV-002": "Recommended price available",
    "CV-003": "Price not below safe floor",
    "CV-004": "Gross margin meets demo threshold",
    "CV-005": "Discount within demo authority",
    "CV-006": "Price deviation from comparable median",
    "CV-007": "Pricing confidence",
    "CV-008": "Cost basis complete",
    "CV-009": "User override deviation",
    "CV-010": "Target price above safe floor",
}


def validate_commercial(
    draft: QuotationDraft,
    pricing_result: PricingResult | None,
) -> CommercialValidationResult:
    rules: list[CommercialRuleResult] = []

    if draft.missing_fields or not draft.selected_product_ids:
        missing = list(draft.missing_fields)
        if not draft.selected_product_ids:
            missing.append("selected product")
        rules.append(
            _rule(
                "CV-001",
                "blocked",
                "Required quotation data is incomplete: "
                + ", ".join(dict.fromkeys(missing)),
            )
        )
    else:
        rules.append(_rule("CV-001", "passed", "All required fields are complete."))

    if pricing_result is None or pricing_result.recommended_unit_price is None:
        rules.append(
            _rule(
                "CV-002",
                "blocked",
                "A deterministic recommended price is unavailable.",
            )
        )
        rules.extend(
            _rule(
                rule_id,
                "not_evaluated",
                "This rule was not evaluated because pricing is unavailable.",
            )
            for rule_id in (
                "CV-003",
                "CV-004",
                "CV-005",
                "CV-006",
                "CV-007",
                "CV-008",
                "CV-009",
                "CV-010",
            )
        )
        return _commercial_result(rules)

    recommended_price = _decimal(pricing_result.recommended_unit_price)
    effective_price = _decimal(
        draft.proposed_unit_price
        if draft.proposed_unit_price is not None
        else pricing_result.recommended_unit_price
    )
    effective_total = effective_price * Decimal(draft.quantity)
    if recommended_price <= 0 or effective_price <= 0 or effective_total <= 0:
        rules.append(
            _rule(
                "CV-002",
                "blocked",
                "Recommended, proposed, and total prices must be greater than zero.",
            )
        )
    else:
        rules.append(
            _rule(
                "CV-002",
                "passed",
                "A positive deterministic recommended price is available.",
            )
        )

    safe_floor = _safe_floor(pricing_result)
    if safe_floor is None:
        rules.append(
            _rule(
                "CV-003",
                "review_required",
                "The safe price floor cannot be fully established.",
            )
        )
    elif effective_price < safe_floor:
        rules.append(
            _rule(
                "CV-003",
                "blocked",
                "The proposed unit price is below the configured safe floor.",
            )
        )
    else:
        rules.append(
            _rule(
                "CV-003",
                "passed",
                "The proposed unit price meets the configured safe floor.",
            )
        )

    if pricing_result.estimated_cost is None:
        rules.append(
            _rule(
                "CV-004",
                "not_evaluated",
                "Gross margin cannot be evaluated because estimated cost is unavailable.",
            )
        )
    else:
        margin = (
            (effective_price - _decimal(pricing_result.estimated_cost))
            / effective_price
            * Decimal("100")
            if effective_price > 0
            else Decimal("-100")
        )
        if margin < _decimal(DEMO_MIN_GROSS_MARGIN_PERCENT):
            rules.append(
                _rule(
                    "CV-004",
                    "blocked",
                    "Gross margin is below the configured demo minimum.",
                )
            )
        elif margin < _decimal(DEMO_REVIEW_MARGIN_PERCENT):
            rules.append(
                _rule(
                    "CV-004",
                    "review_required",
                    "Gross margin meets the minimum but is within the demo review band.",
                )
            )
        else:
            rules.append(
                _rule(
                    "CV-004",
                    "passed",
                    "Gross margin is above the configured demo review threshold.",
                )
            )

    reference_list = _positive_decimal(pricing_result.reference_list_price)
    if reference_list is None:
        rules.append(
            _rule(
                "CV-005",
                "review_required",
                "Discount authority cannot be evaluated without a reference list price.",
            )
        )
    else:
        discount = (
            (reference_list - effective_price) / reference_list * Decimal("100")
        )
        if discount > _decimal(DEMO_MANAGER_DISCOUNT_LIMIT_PERCENT):
            rules.append(
                _rule(
                    "CV-005",
                    "blocked",
                    "Discount exceeds the configured demo manager limit.",
                )
            )
        elif discount > _decimal(DEMO_AUTO_DISCOUNT_LIMIT_PERCENT):
            rules.append(
                _rule(
                    "CV-005",
                    "review_required",
                    "Discount exceeds the configured demo automatic-authority limit.",
                )
            )
        else:
            rules.append(
                _rule(
                    "CV-005",
                    "passed",
                    "Discount is within the configured demo automatic-authority limit.",
                )
            )

    comparable_median = _positive_decimal(
        pricing_result.comparable_median_price
        or pricing_result.reference_net_price
    )
    if comparable_median is None:
        rules.append(
            _rule(
                "CV-006",
                "review_required",
                "Comparable-price deviation cannot be evaluated.",
            )
        )
    else:
        deviation = (
            abs(effective_price - comparable_median)
            / comparable_median
            * Decimal("100")
        )
        if deviation >= _decimal(DEMO_PRICE_DEVIATION_BLOCK_PERCENT):
            rules.append(
                _rule(
                    "CV-006",
                    "blocked",
                    "Price deviation from the comparable median is extreme.",
                )
            )
        elif deviation >= _decimal(DEMO_PRICE_DEVIATION_REVIEW_PERCENT):
            rules.append(
                _rule(
                    "CV-006",
                    "review_required",
                    "Price deviation from the comparable median requires review.",
                )
            )
        else:
            rules.append(
                _rule(
                    "CV-006",
                    "passed",
                    "Price deviation from the comparable median is within tolerance.",
                )
            )

    confidence = pricing_result.confidence_label.casefold()
    if confidence == "low":
        rules.append(
            _rule(
                "CV-007",
                "review_required",
                "Pricing confidence is low.",
            )
        )
    elif confidence == "medium":
        rules.append(
            _rule(
                "CV-007",
                "warning",
                "Pricing confidence is medium.",
            )
        )
    else:
        rules.append(_rule("CV-007", "passed", "Pricing confidence is high."))

    if (
        pricing_result.estimated_cost is None
        or not pricing_result.cost_basis_complete
    ):
        rules.append(
            _rule(
                "CV-008",
                "review_required",
                "Estimated cost is missing or the cost basis is incomplete.",
            )
        )
    else:
        rules.append(
            _rule(
                "CV-008",
                "passed",
                "The configured demo cost basis is complete.",
            )
        )

    if draft.proposed_unit_price is None:
        rules.append(
            _rule(
                "CV-009",
                "not_evaluated",
                "No user price override was supplied.",
            )
        )
    else:
        override_deviation = (
            abs(effective_price - recommended_price)
            / recommended_price
            * Decimal("100")
            if recommended_price > 0
            else Decimal("100")
        )
        if override_deviation >= _decimal(DEMO_PRICE_DEVIATION_BLOCK_PERCENT):
            rules.append(
                _rule(
                    "CV-009",
                    "blocked",
                    "The user price override differs extremely from the recommendation.",
                )
            )
        elif override_deviation >= _decimal(
            DEMO_PRICE_DEVIATION_REVIEW_PERCENT
        ):
            rules.append(
                _rule(
                    "CV-009",
                    "review_required",
                    "The user price override differs materially from the recommendation.",
                )
            )
        else:
            rules.append(
                _rule(
                    "CV-009",
                    "passed",
                    "The user price override is within the configured tolerance.",
                )
            )

    if draft.target_price is None:
        rules.append(
            _rule(
                "CV-010",
                "not_evaluated",
                "No customer target price was supplied.",
            )
        )
    elif safe_floor is None:
        rules.append(
            _rule(
                "CV-010",
                "review_required",
                "The target price cannot be compared because the safe floor is incomplete.",
            )
        )
    elif _decimal(draft.target_price) < safe_floor:
        rules.append(
            _rule(
                "CV-010",
                "blocked",
                "The customer target price is below the configured safe floor.",
            )
        )
    else:
        rules.append(
            _rule(
                "CV-010",
                "passed",
                "The customer target price is not below the configured safe floor.",
            )
        )

    return _commercial_result(rules, pricing_result.warnings)


def evaluate_quotation(
    draft: QuotationDraft,
    pricing_result: PricingResult | None,
    technical_validation: TechnicalValidationResult,
) -> CombinedDecision:
    commercial = validate_commercial(draft, pricing_result)
    return combine_validation_decision(technical_validation, commercial)


def combine_validation_decision(
    technical: TechnicalValidationResult,
    commercial: CommercialValidationResult,
) -> CombinedDecision:
    triggered_rule_ids = [
        result.rule_id
        for result in commercial.rule_results
        if result.status in {"blocked", "review_required", "warning"}
    ]
    technical_blocked = technical.status == "invalid" or bool(technical.errors)
    technical_review = (
        technical.status in {"valid_with_warnings", "not_fully_evaluated"}
        or bool(technical.warnings)
        or bool(technical.not_evaluated_checks)
    )

    if technical_blocked:
        triggered_rule_ids.insert(0, "TECH-INVALID")
    elif technical.warnings:
        triggered_rule_ids.insert(0, "TECH-WARNING")
    if technical.not_evaluated_checks:
        triggered_rule_ids.insert(0, "TECH-INCOMPLETE")

    if technical_blocked or commercial.status == "blocked":
        return CombinedDecision(
            status="blocked",
            summary="The quotation has blocking technical or commercial issues.",
            triggered_rule_ids=list(dict.fromkeys(triggered_rule_ids)),
            approval_required=False,
            recommended_next_action=(
                "Correct the blocking issues and rerun pricing and validation."
            ),
        )
    if technical_review or commercial.status == "review_required":
        return CombinedDecision(
            status="review_required",
            summary="The quotation requires review before it can proceed.",
            triggered_rule_ids=list(dict.fromkeys(triggered_rule_ids)),
            approval_required=True,
            recommended_next_action=(
                "Resolve incomplete checks or route the quotation for human review."
            ),
        )
    if commercial.status == "valid_with_warnings":
        return CombinedDecision(
            status="pass_with_warnings",
            summary="The quotation passes with non-blocking warnings.",
            triggered_rule_ids=list(dict.fromkeys(triggered_rule_ids)),
            approval_required=False,
            recommended_next_action="Review the warnings before continuing.",
        )
    return CombinedDecision(
        status="pass",
        summary="The quotation passed all evaluated technical and commercial checks.",
        triggered_rule_ids=[],
        approval_required=False,
        recommended_next_action="Continue to human approval.",
    )


def _rule(rule_id: str, status: str, message: str) -> CommercialRuleResult:
    return CommercialRuleResult(
        rule_id=rule_id,
        name=COMMERCIAL_RULE_NAMES[rule_id],
        status=status,
        message=message,
    )


def _commercial_result(
    rules: list[CommercialRuleResult],
    pricing_warnings: list[str] | None = None,
) -> CommercialValidationResult:
    blocked = [rule for rule in rules if rule.status == "blocked"]
    reviews = [rule for rule in rules if rule.status == "review_required"]
    warnings = [rule for rule in rules if rule.status == "warning"]
    if blocked:
        status = "blocked"
    elif reviews:
        status = "review_required"
    elif warnings or pricing_warnings:
        status = "valid_with_warnings"
    else:
        status = "valid"

    return CommercialValidationResult(
        status=status,
        errors=[rule.message for rule in blocked],
        warnings=[
            *(rule.message for rule in (*reviews, *warnings)),
            *(pricing_warnings or []),
        ],
        approval_required=status == "review_required",
        approval_reasons=[rule.message for rule in reviews],
        rule_results=rules,
        evaluated_rules=[
            rule.rule_id for rule in rules if rule.status != "not_evaluated"
        ],
    )


def _safe_floor(pricing_result: PricingResult) -> Decimal | None:
    floors = [
        value
        for value in (
            _positive_decimal(pricing_result.minimum_price_floor),
            _positive_decimal(pricing_result.gross_margin_floor),
        )
        if value is not None
    ]
    return max(floors) if floors else None


def _positive_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    normalized = _decimal(value)
    return normalized if normalized > 0 else None


def _decimal(value: float | int) -> Decimal:
    return Decimal(str(value))
