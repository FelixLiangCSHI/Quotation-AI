"""Deterministic multi-line quotation pricing analysis (Phase 5).

Three analysis levels are produced from one deterministic pass:

* line-level analysis for every quotation line item,
* bundle/category-level roll-ups,
* one quotation-level analysis.

Quotation-level gross margin is always calculated from quotation totals::

    gross_margin_amount  = total_revenue - total_cost
    gross_margin_percent = gross_margin_amount / total_revenue * 100

It is never an average of line-level margin percentages. Every monetary value
is a :class:`~decimal.Decimal` internally and is stored as an exact decimal
string, so the commercial gate never evaluates a rounded display value.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Mapping
from uuid import uuid4

from app.commercial_policy import (
    CommercialPolicyVersion,
    active_commercial_policy,
)
from app.config import SAP_BASE_CURRENCY
from app.pricing_engine import PricingEngine, calculate_demo_base_cost
from app.quotation_models import (
    BundlePricingAnalysis,
    LineItemCategory,
    LinePricingAnalysis,
    QuotationDraft,
    QuotationPricingAnalysis,
)

#: Internal working precision for money. Display rounding happens elsewhere.
MONEY_PRECISION = Decimal("0.0001")
#: Internal working precision for percentages used by the margin gate.
PERCENT_PRECISION = Decimal("0.000001")
#: Display precision. Never used for rule evaluation.
DISPLAY_MONEY_PRECISION = Decimal("0.01")
DISPLAY_PERCENT_PRECISION = Decimal("0.01")

MARGIN_STATUS_AVAILABLE = "available"
MARGIN_STATUS_UNAVAILABLE = "unavailable"

#: Missing-data flags.
FLAG_MISSING_COST = "missing_cost_basis"
FLAG_MISSING_PRICE = "missing_unit_price"
FLAG_ZERO_COST_ALLOWED = "zero_cost_permitted_by_policy"
FLAG_NO_COMPARABLES = "no_comparable_pricing_records"

#: Blocking reasons.
BLOCK_INVALID_QUANTITY = "invalid_quantity"
BLOCK_NEGATIVE_PRICE = "negative_price"
BLOCK_MISSING_PRICE = "missing_price"
BLOCK_MISSING_COST = "missing_cost_basis"
BLOCK_ZERO_REVENUE = "zero_quotation_revenue"
BLOCK_MIXED_CURRENCY = "mixed_currency_basis"
BLOCK_NO_LINE_ITEMS = "no_quotation_line_items"


class PricingAnalysisError(ValueError):
    """Raised when an analysis request itself is malformed."""


@dataclass(frozen=True)
class LineCostBasis:
    """Trusted cost basis for one line item."""

    unit_cost: Decimal | None
    source: str
    data_version: str = ""


def new_pricing_run_id() -> str:
    return f"PR-{uuid4().hex[:12].upper()}"


def analyse_quotation_pricing(
    draft: QuotationDraft,
    *,
    pricing_engine: PricingEngine | None = None,
    policy: CommercialPolicyVersion | None = None,
    cost_overrides: Mapping[str, Decimal | float | str | None] | None = None,
    pricing_data_version: str = "",
    quotation_version: str = "",
    pricing_run_id: str | None = None,
) -> QuotationPricingAnalysis:
    """Price every line item and roll the result up to the quotation."""

    policy = policy or active_commercial_policy()
    quotation_currency = (draft.currency or SAP_BASE_CURRENCY).strip().upper()
    analysis = QuotationPricingAnalysis(
        quotation_id=draft.quotation_id,
        pricing_run_id=pricing_run_id or new_pricing_run_id(),
        quotation_version=quotation_version,
        currency=quotation_currency,
        pricing_data_version=pricing_data_version,
        margin_status=MARGIN_STATUS_UNAVAILABLE,
    )
    analysis.assumptions.append(
        "Every line is priced deterministically; AI output cannot change any "
        "cost, price, margin or decision."
    )
    analysis.assumptions.append(
        "Quotation gross margin uses quotation totals, not an average of "
        "line-level margin percentages."
    )
    if not policy.allow_transfer_price_as_cogs:
        analysis.assumptions.append(
            "Transfer price is never substituted for COGS: no versioned "
            "company policy permits that substitution."
        )

    line_items = list(draft.line_items)
    if not line_items:
        analysis.blocking_reasons.append(BLOCK_NO_LINE_ITEMS)
        analysis.warnings.append(
            "The quotation has no line items, so no margin can be calculated."
        )
        return analysis

    overrides = {
        str(key): _to_decimal(value)
        for key, value in (cost_overrides or {}).items()
    }

    total_revenue = Decimal("0")
    total_cost = Decimal("0")
    cost_basis_trusted = True

    for item in line_items:
        line = _analyse_line(
            item,
            draft=draft,
            quotation_currency=quotation_currency,
            policy=policy,
            pricing_engine=pricing_engine,
            override_cost=overrides.get(item.line_id),
            pricing_data_version=pricing_data_version,
        )
        analysis.line_analyses.append(line)
        for reason in line.blocking_reasons:
            if reason not in analysis.blocking_reasons:
                analysis.blocking_reasons.append(reason)
        for flag in line.missing_data_flags:
            if flag not in analysis.missing_data_flags:
                analysis.missing_data_flags.append(flag)

        if line.line_revenue is not None:
            total_revenue += Decimal(line.line_revenue)
        if line.total_cost is not None:
            total_cost += Decimal(line.total_cost)
        elif _is_material_revenue_line(line, policy):
            cost_basis_trusted = False

    analysis.bundle_analyses = _build_bundles(
        analysis.line_analyses, quotation_currency
    )
    analysis.total_revenue = _money_text(total_revenue)
    analysis.total_cost = _money_text(total_cost)

    if not cost_basis_trusted:
        if BLOCK_MISSING_COST not in analysis.blocking_reasons:
            analysis.blocking_reasons.append(BLOCK_MISSING_COST)
        analysis.warnings.append(
            "A material revenue line has no trusted cost basis, so no "
            "quotation-level margin can be produced."
        )
        return analysis

    if total_revenue <= 0:
        if BLOCK_ZERO_REVENUE not in analysis.blocking_reasons:
            analysis.blocking_reasons.append(BLOCK_ZERO_REVENUE)
        analysis.warnings.append(
            "Quotation revenue is zero or negative, so gross margin cannot be "
            "calculated."
        )
        return analysis

    if analysis.blocking_reasons:
        return analysis

    margin_amount = total_revenue - total_cost
    margin_percent = margin_amount / total_revenue * Decimal("100")
    analysis.gross_margin_amount = _money_text(margin_amount)
    analysis.gross_margin_percent = _percent_text(margin_percent)
    analysis.margin_status = MARGIN_STATUS_AVAILABLE
    return analysis


def _analyse_line(
    item,
    *,
    draft: QuotationDraft,
    quotation_currency: str,
    policy: CommercialPolicyVersion,
    pricing_engine: PricingEngine | None,
    override_cost: Decimal | None,
    pricing_data_version: str,
) -> LinePricingAnalysis:
    line = LinePricingAnalysis(
        line_id=item.line_id,
        product_id=item.product_id,
        description=item.description,
        line_item_type=LineItemCategory(item.category).value,
        quantity=int(item.quantity),
        is_optional=bool(item.is_optional),
        currency=(item.currency or quotation_currency).strip().upper(),
        pricing_confidence=item.pricing_confidence or "",
        pricing_data_version=item.pricing_data_version or pricing_data_version,
        comparable_count=int(item.comparable_count or 0),
    )
    line.list_price = _optional_money_text(_to_decimal(item.list_price))
    line.comparable_median_price = _optional_money_text(
        _to_decimal(item.comparable_median_price)
    )
    line.recommended_unit_price = _optional_money_text(
        _to_decimal(item.recommended_unit_price)
    )
    line.approved_unit_price = _optional_money_text(
        _to_decimal(item.approved_unit_price)
    )

    if line.currency != quotation_currency:
        line.blocking_reasons.append(BLOCK_MIXED_CURRENCY)

    if line.quantity < 1:
        line.blocking_reasons.append(BLOCK_INVALID_QUANTITY)

    unit_price = _to_decimal(item.proposed_unit_price)
    if unit_price is None:
        line.missing_data_flags.append(FLAG_MISSING_PRICE)
        line.blocking_reasons.append(BLOCK_MISSING_PRICE)
    else:
        line.proposed_unit_price = _money_text(unit_price)
        if unit_price < 0:
            line.blocking_reasons.append(BLOCK_NEGATIVE_PRICE)

    for value, reason in (
        (_to_decimal(item.list_price), BLOCK_NEGATIVE_PRICE),
        (_to_decimal(item.recommended_unit_price), BLOCK_NEGATIVE_PRICE),
        (_to_decimal(item.approved_unit_price), BLOCK_NEGATIVE_PRICE),
    ):
        if value is not None and value < 0 and reason not in line.blocking_reasons:
            line.blocking_reasons.append(reason)

    quantity = Decimal(max(line.quantity, 0))
    if unit_price is not None and line.quantity >= 1:
        line.line_revenue = _money_text(unit_price * quantity)

    basis = _resolve_cost_basis(
        item,
        override_cost=override_cost,
        policy=policy,
        pricing_engine=pricing_engine,
        draft=draft,
    )
    if basis.data_version and not line.pricing_data_version:
        line.pricing_data_version = basis.data_version
    if basis.unit_cost is None:
        line.missing_data_flags.append(FLAG_MISSING_COST)
        if basis.source == "no_comparables":
            line.missing_data_flags.append(FLAG_NO_COMPARABLES)
    else:
        if basis.unit_cost < 0:
            line.blocking_reasons.append(BLOCK_NEGATIVE_PRICE)
        if basis.unit_cost == 0 and basis.source == "policy_zero_cost":
            line.missing_data_flags.append(FLAG_ZERO_COST_ALLOWED)
        line.estimated_unit_cost = _money_text(basis.unit_cost)
        line.total_cost = _money_text(basis.unit_cost * quantity)

    if (
        line.line_revenue is not None
        and line.total_cost is not None
        and not line.blocking_reasons
    ):
        revenue = Decimal(line.line_revenue)
        cost = Decimal(line.total_cost)
        margin = revenue - cost
        line.gross_margin_amount = _money_text(margin)
        if revenue > 0:
            line.gross_margin_percent = _percent_text(
                margin / revenue * Decimal("100")
            )
    return line


def _resolve_cost_basis(
    item,
    *,
    override_cost: Decimal | None,
    policy: CommercialPolicyVersion,
    pricing_engine: PricingEngine | None,
    draft: QuotationDraft,
) -> LineCostBasis:
    """Resolve a trusted unit cost. A missing cost is never treated as zero."""

    if override_cost is not None:
        return LineCostBasis(unit_cost=override_cost, source="explicit_override")

    declared = _to_decimal(item.estimated_unit_cost)
    if declared is not None:
        return LineCostBasis(
            unit_cost=declared,
            source=item.cost_source or "line_item_cost_basis",
        )

    category = LineItemCategory(item.category).value
    if policy.permits_zero_cost(category):
        return LineCostBasis(unit_cost=Decimal("0"), source="policy_zero_cost")

    if pricing_engine is not None and item.product_id:
        comparables = pricing_engine.find_comparables(
            item.product_id,
            product_description=item.description,
        )
        costs = [
            cost
            for cost in (
                calculate_demo_base_cost(match.record)
                for match in comparables
                if match.tier >= 2
            )
            if cost is not None
        ]
        if costs:
            costs.sort()
            middle = len(costs) // 2
            unit_cost = (
                costs[middle]
                if len(costs) % 2
                else (costs[middle - 1] + costs[middle]) / Decimal("2")
            )
            return LineCostBasis(
                unit_cost=unit_cost, source="comparable_cost_median"
            )
        return LineCostBasis(unit_cost=None, source="no_comparables")

    return LineCostBasis(unit_cost=None, source="unavailable")


def _is_material_revenue_line(
    line: LinePricingAnalysis,
    policy: CommercialPolicyVersion,
) -> bool:
    """A revenue-bearing line that must supply a trusted cost basis."""

    if line.line_revenue is None:
        return False
    if Decimal(line.line_revenue) <= 0:
        return False
    return not policy.permits_zero_cost(line.line_item_type)


def _build_bundles(
    lines: Iterable[LinePricingAnalysis],
    quotation_currency: str,
) -> list[BundlePricingAnalysis]:
    grouped: dict[str, list[LinePricingAnalysis]] = {}
    for line in lines:
        grouped.setdefault(line.line_item_type, []).append(line)

    bundles: list[BundlePricingAnalysis] = []
    for bundle_key in sorted(grouped):
        members = grouped[bundle_key]
        bundle = BundlePricingAnalysis(
            bundle_key=bundle_key,
            line_ids=[line.line_id for line in members],
            line_count=len(members),
            currency=quotation_currency,
        )
        revenue = Decimal("0")
        cost = Decimal("0")
        cost_complete = True
        for line in members:
            if line.line_revenue is not None:
                revenue += Decimal(line.line_revenue)
            if line.total_cost is not None:
                cost += Decimal(line.total_cost)
            else:
                cost_complete = False
            for flag in line.missing_data_flags:
                if flag not in bundle.missing_data_flags:
                    bundle.missing_data_flags.append(flag)
        bundle.total_revenue = _money_text(revenue)
        bundle.total_cost = _money_text(cost) if cost_complete else None
        if cost_complete and revenue > 0:
            margin = revenue - cost
            bundle.gross_margin_amount = _money_text(margin)
            bundle.gross_margin_percent = _percent_text(
                margin / revenue * Decimal("100")
            )
            bundle.margin_status = MARGIN_STATUS_AVAILABLE
        else:
            bundle.margin_status = MARGIN_STATUS_UNAVAILABLE
        bundles.append(bundle)
    return bundles


# --- decimal helpers -------------------------------------------------------


def _to_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _money_text(value: Decimal) -> str:
    return str(value.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP))


def _optional_money_text(value: Decimal | None) -> str | None:
    return None if value is None else _money_text(value)


def _percent_text(value: Decimal) -> str:
    return str(value.quantize(PERCENT_PRECISION, rounding=ROUND_HALF_UP))


def display_money(value: str | None) -> str | None:
    """Round a stored decimal string for display only."""

    if value is None:
        return None
    return str(Decimal(value).quantize(DISPLAY_MONEY_PRECISION, rounding=ROUND_HALF_UP))


def display_percent(value: str | None) -> str | None:
    """Round a stored decimal string for display only."""

    if value is None:
        return None
    return str(Decimal(value).quantize(DISPLAY_PERCENT_PRECISION, rounding=ROUND_HALF_UP))
