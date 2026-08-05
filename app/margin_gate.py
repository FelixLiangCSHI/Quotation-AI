"""Deterministic commercial margin gate (Phase 5).

The decision is produced by ordered, deterministic steps. No AI participates in
producing ``pass``, ``review_required`` or ``blocked``.

Evaluation order::

    1. Validate quotation completeness and monetary values.
    2. Run technical compatibility validation.
    3. Validate that a trustworthy cost and currency basis exists.
    4. Calculate line and quotation totals.
    5. Calculate quotation-level gross margin.
    6. Load the active CommercialPolicyVersion.
    7. Apply the exact margin threshold.
    8. Persist the complete rule trace and result.

The threshold value itself lives only in
:mod:`app.commercial_policy`. The comparison always uses the full internal
decimal margin, never a rounded display value.
"""

from __future__ import annotations

from decimal import Decimal

from app.commercial_policy import (
    ComparisonOperator,
    CommercialPolicyVersion,
    active_commercial_policy,
    policy_key,
)
from app.quotation_models import (
    CombinedDecision,
    QuotationPricingAnalysis,
    RuleTraceEntry,
    TechnicalValidationResult,
    utc_now,
)
from app.quotation_pricing import (
    BLOCK_INVALID_QUANTITY,
    BLOCK_MISSING_COST,
    BLOCK_MISSING_PRICE,
    BLOCK_MIXED_CURRENCY,
    BLOCK_NEGATIVE_PRICE,
    BLOCK_NO_LINE_ITEMS,
    BLOCK_ZERO_REVENUE,
    MARGIN_STATUS_AVAILABLE,
    display_percent,
)

RULE_TECH_001 = "TECH-001"
RULE_DATA_001 = "DATA-001"
RULE_DATA_002 = "DATA-002"
RULE_DATA_003 = "DATA-003"
RULE_COMM_MARGIN_001 = "COMM-MARGIN-001"
RULE_COMM_MARGIN_002 = "COMM-MARGIN-002"

RULE_NAMES = {
    RULE_TECH_001: "Deterministic technical incompatibility",
    RULE_DATA_001: "Required quotation data incomplete",
    RULE_DATA_002: "Trusted cost basis unavailable",
    RULE_DATA_003: "Invalid or mixed currency basis",
    RULE_COMM_MARGIN_001: "Gross margin above configured pass threshold",
    RULE_COMM_MARGIN_002: (
        "Gross margin at or below configured pass threshold; human approval "
        "required"
    ),
}

STATUS_PASS = "pass"
STATUS_REVIEW_REQUIRED = "review_required"
STATUS_BLOCKED = "blocked"

#: Blocking reasons that mean required quotation data is incomplete or invalid.
_DATA_001_REASONS = frozenset(
    {
        BLOCK_NO_LINE_ITEMS,
        BLOCK_MISSING_PRICE,
        BLOCK_INVALID_QUANTITY,
        BLOCK_NEGATIVE_PRICE,
        BLOCK_ZERO_REVENUE,
    }
)


def evaluate_commercial_decision(
    pricing: QuotationPricingAnalysis,
    technical: TechnicalValidationResult | None = None,
    *,
    policy: CommercialPolicyVersion | None = None,
    technical_validation_run_id: str = "",
) -> CombinedDecision:
    """Apply the deterministic decision steps and return the final result."""

    policy = policy or active_commercial_policy()
    trace: list[RuleTraceEntry] = []
    blocking_reasons: list[str] = []
    review_reasons: list[str] = []
    triggered_rule_ids: list[str] = []

    reasons = set(pricing.blocking_reasons)

    # Step 1 - quotation completeness and monetary values.
    data_problems = sorted(reasons.intersection(_DATA_001_REASONS))
    if data_problems:
        blocking_reasons.extend(data_problems)
        _add(
            trace,
            triggered_rule_ids,
            RULE_DATA_001,
            1,
            "blocked",
            "Required quotation data is incomplete or contains invalid "
            "monetary values: " + ", ".join(data_problems),
            {"blocking_reasons": data_problems},
        )
    else:
        _add(
            trace,
            None,
            RULE_DATA_001,
            1,
            "passed",
            "Quotation completeness and monetary values are valid.",
            {},
        )

    # Step 2 - technical compatibility validation.
    technical_blocked = bool(
        technical is not None
        and (technical.status == "invalid" or technical.errors)
    )
    if technical_blocked:
        blocking_reasons.append("technical_incompatibility")
        _add(
            trace,
            triggered_rule_ids,
            RULE_TECH_001,
            2,
            "blocked",
            "Deterministic technical incompatibility detected.",
            {"technical_errors": list(technical.errors)},
        )
    else:
        _add(
            trace,
            None,
            RULE_TECH_001,
            2,
            "passed" if technical is not None else "not_evaluated",
            (
                "No deterministic technical incompatibility was detected."
                if technical is not None
                else "No technical validation result was supplied."
            ),
            {},
        )

    # Step 3 - trustworthy cost and currency basis.
    if BLOCK_MIXED_CURRENCY in reasons:
        blocking_reasons.append(BLOCK_MIXED_CURRENCY)
        _add(
            trace,
            triggered_rule_ids,
            RULE_DATA_003,
            3,
            "blocked",
            "Monetary values are not normalised into one quotation currency.",
            {"quotation_currency": pricing.currency},
        )
    else:
        _add(
            trace,
            None,
            RULE_DATA_003,
            3,
            "passed",
            f"All monetary values are expressed in {pricing.currency}.",
            {},
        )

    if BLOCK_MISSING_COST in reasons:
        blocking_reasons.append(BLOCK_MISSING_COST)
        _add(
            trace,
            triggered_rule_ids,
            RULE_DATA_002,
            3,
            "blocked",
            "A material revenue line has no trusted cost basis. A missing "
            "cost is never treated as zero.",
            {"missing_data_flags": list(pricing.missing_data_flags)},
        )
    else:
        _add(
            trace,
            None,
            RULE_DATA_002,
            3,
            "passed",
            "A trusted cost basis exists for every material revenue line.",
            {},
        )

    # Steps 4 and 5 - totals and quotation-level gross margin.
    margin_available = (
        pricing.margin_status == MARGIN_STATUS_AVAILABLE
        and pricing.gross_margin_percent is not None
    )
    if not margin_available and not blocking_reasons:
        blocking_reasons.append("gross_margin_unavailable")
        _add(
            trace,
            triggered_rule_ids,
            RULE_DATA_002,
            5,
            "blocked",
            "A trustworthy quotation-level gross margin could not be produced.",
            {"margin_status": pricing.margin_status},
        )

    threshold = policy.pass_margin_threshold_percent
    policy_version_id = policy_key(policy)

    if blocking_reasons:
        _add(
            trace,
            None,
            RULE_COMM_MARGIN_001,
            7,
            "not_evaluated",
            "The margin gate was not evaluated because the quotation is "
            "blocked.",
            {},
        )
        return _decision(
            status=STATUS_BLOCKED,
            summary=(
                "The quotation is blocked. It cannot be approved until the "
                "blocking issues are corrected and pricing and validation are "
                "rerun."
            ),
            next_action=(
                "Correct the blocking issues, then rerun pricing and "
                "validation."
            ),
            approval_required=False,
            policy=policy,
            policy_version_id=policy_version_id,
            evaluated_margin=None,
            threshold=threshold,
            blocking_reasons=_unique(blocking_reasons),
            review_reasons=review_reasons,
            triggered_rule_ids=_unique(triggered_rule_ids),
            trace=trace,
            pricing=pricing,
            technical_validation_run_id=technical_validation_run_id,
        )

    # Steps 6 and 7 - load the active policy and apply the exact threshold to
    # the full internal decimal margin value.
    margin = Decimal(pricing.gross_margin_percent)
    passes = _compare(margin, threshold, policy.comparison_operator)
    rule_id = RULE_COMM_MARGIN_001 if passes else RULE_COMM_MARGIN_002
    triggered_rule_ids.append(rule_id)
    _add(
        trace,
        None,
        rule_id,
        7,
        "passed" if passes else "review_required",
        (
            f"Gross margin {margin}% is greater than the configured pass "
            f"threshold {threshold}%."
            if passes
            else f"Gross margin {margin}% is not greater than the configured "
            f"pass threshold {threshold}% and requires human approval."
        ),
        {
            "evaluated_margin_percent": str(margin),
            "threshold_percent": str(threshold),
            "comparison_operator": policy.comparison_operator.value,
            "policy_version_id": policy_version_id,
        },
    )

    if passes:
        return _decision(
            status=STATUS_PASS,
            summary=(
                "The quotation passed the provisional commercial margin gate. "
                "This is not an autonomous customer send; an authorised human "
                "confirmation step is still required."
            ),
            next_action=(
                "Route the quotation to an authorised human confirmation or "
                "approval step."
            ),
            approval_required=False,
            policy=policy,
            policy_version_id=policy_version_id,
            evaluated_margin=margin,
            threshold=threshold,
            blocking_reasons=[],
            review_reasons=[],
            triggered_rule_ids=_unique(triggered_rule_ids),
            trace=trace,
            pricing=pricing,
            technical_validation_run_id=technical_validation_run_id,
        )

    review_reasons.append(
        f"Gross margin is at or below the configured pass threshold of "
        f"{threshold}%."
    )
    return _decision(
        status=STATUS_REVIEW_REQUIRED,
        summary=(
            "The quotation did not clear the provisional commercial margin "
            "gate and must enter the human approval workflow."
        ),
        next_action="Create a human approval task for this quotation.",
        approval_required=True,
        policy=policy,
        policy_version_id=policy_version_id,
        evaluated_margin=margin,
        threshold=threshold,
        blocking_reasons=[],
        review_reasons=review_reasons,
        triggered_rule_ids=_unique(triggered_rule_ids),
        trace=trace,
        pricing=pricing,
        technical_validation_run_id=technical_validation_run_id,
    )


def decision_display_margin(decision: CombinedDecision) -> str | None:
    """Rounded margin for display only. Never used to decide anything."""

    return display_percent(decision.evaluated_margin_percent)


def _compare(
    margin: Decimal,
    threshold: Decimal,
    operator: ComparisonOperator,
) -> bool:
    if operator is ComparisonOperator.GREATER_THAN:
        return margin > threshold
    raise ValueError(f"Unsupported comparison operator: {operator}")


def _decision(
    *,
    status: str,
    summary: str,
    next_action: str,
    approval_required: bool,
    policy: CommercialPolicyVersion,
    policy_version_id: str,
    evaluated_margin: Decimal | None,
    threshold: Decimal,
    blocking_reasons: list[str],
    review_reasons: list[str],
    triggered_rule_ids: list[str],
    trace: list[RuleTraceEntry],
    pricing: QuotationPricingAnalysis,
    technical_validation_run_id: str,
) -> CombinedDecision:
    return CombinedDecision(
        status=status,
        summary=summary,
        triggered_rule_ids=triggered_rule_ids,
        approval_required=approval_required,
        recommended_next_action=next_action,
        policy_version_id=policy_version_id,
        policy_name=policy.policy_name,
        evaluated_margin_percent=(
            None if evaluated_margin is None else str(evaluated_margin)
        ),
        threshold_percent=str(threshold),
        comparison_operator=policy.comparison_operator.value,
        blocking_reasons=blocking_reasons,
        review_reasons=review_reasons,
        rule_trace=trace,
        calculated_at=utc_now(),
        pricing_run_id=pricing.pricing_run_id,
        technical_validation_run_id=technical_validation_run_id,
        quotation_version=pricing.quotation_version,
    )


def _add(
    trace: list[RuleTraceEntry],
    triggered: list[str] | None,
    rule_id: str,
    step: int,
    outcome: str,
    message: str,
    inputs: dict,
) -> None:
    trace.append(
        RuleTraceEntry(
            rule_id=rule_id,
            name=RULE_NAMES[rule_id],
            outcome=outcome,
            message=message,
            step=step,
            inputs=inputs,
        )
    )
    if triggered is not None:
        triggered.append(rule_id)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
