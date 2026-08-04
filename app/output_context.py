from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.config import DEMO_QUOTATION_VALIDITY_DAYS
from app.quotation_models import (
    ApprovalStatus,
    QuotationWorkflowState,
    utc_now,
)


APPROVED_STATUSES = frozenset(
    {
        ApprovalStatus.APPROVED,
        ApprovalStatus.APPROVED_WITH_OVERRIDE,
    }
)


class OutputGenerationError(ValueError):
    pass


@dataclass(frozen=True)
class QuotationOutputContext:
    quotation_id: str
    customer_name: str
    product_id: str
    product_description: str
    quantity: int
    currency: str
    recommended_unit_price: float
    final_unit_price: float
    total_price: float
    quotation_date: date
    validity_date: date
    approval_status: str
    approver: str
    approved_at: str
    region: str
    delivery_location: str
    delivery_assumption: str
    incoterm: str
    confidence: str
    validation_status: str
    review_reasons: tuple[str, ...]
    requested_action: str
    margin_summary: str


def build_output_context(
    state: QuotationWorkflowState,
    *,
    require_approved: bool = False,
    as_of: date | None = None,
    validity_days: int = DEMO_QUOTATION_VALIDITY_DAYS,
) -> QuotationOutputContext:
    pricing = state.pricing_result
    if pricing is None or pricing.recommended_unit_price is None:
        raise OutputGenerationError(
            "Current pricing is required before generating quotation outputs."
        )
    if state.validation_stale or state.combined_decision is None:
        raise OutputGenerationError(
            "Current validation is required before generating quotation outputs."
        )
    if require_approved and state.approval.status not in APPROVED_STATUSES:
        raise OutputGenerationError(
            "Customer quotation outputs require an approved quotation."
        )
    if state.draft.quantity <= 0:
        raise OutputGenerationError("Quotation quantity must be greater than zero.")

    recommended_price = float(pricing.recommended_unit_price)
    final_price = (
        float(state.approval.final_price)
        if state.approval.final_price is not None
        else float(state.draft.proposed_unit_price or recommended_price)
    )
    if final_price <= 0:
        raise OutputGenerationError(
            "The proposed or approved unit price must be greater than zero."
        )

    quotation_date = as_of or (
        state.approval.timestamp.date()
        if state.approval.timestamp is not None
        else utc_now().date()
    )
    product_ids = state.draft.selected_product_ids or pricing.selected_product_ids
    if not product_ids:
        raise OutputGenerationError(
            "A selected product is required before generating quotation outputs."
        )
    review_reasons = _review_reasons(state)
    decision = state.combined_decision
    margin = pricing.gross_margin_percent

    return QuotationOutputContext(
        quotation_id=state.draft.quotation_id,
        customer_name=state.draft.customer_name or "Customer",
        product_id=", ".join(product_ids),
        product_description=_selected_product_description(state, product_ids),
        quantity=state.draft.quantity,
        currency=pricing.currency or state.draft.currency,
        recommended_unit_price=_money(recommended_price),
        final_unit_price=_money(final_price),
        total_price=_money(final_price * state.draft.quantity),
        quotation_date=quotation_date,
        validity_date=quotation_date + timedelta(days=validity_days),
        approval_status=state.approval.status.value,
        approver=state.approval.actor or state.approval.actor_role,
        approved_at=(
            state.approval.timestamp.isoformat()
            if state.approval.timestamp is not None
            else ""
        ),
        region=state.draft.region or "Not specified",
        delivery_location=state.draft.delivery_location or "Not specified",
        delivery_assumption=_delivery_assumption(state),
        incoterm=state.draft.incoterm or "To be confirmed",
        confidence=pricing.confidence_label or "Not available",
        validation_status=decision.status,
        review_reasons=review_reasons,
        requested_action=decision.recommended_next_action,
        margin_summary=(
            "Not available" if margin is None else f"{margin:.2f}%"
        ),
    )


def format_money(value: float, currency: str) -> str:
    return f"{currency} {value:,.2f}"


def _money(value: float) -> float:
    return float(
        Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def _review_reasons(state: QuotationWorkflowState) -> tuple[str, ...]:
    reasons: list[str] = []
    commercial = state.commercial_validation
    if commercial is not None:
        reasons.extend(commercial.approval_reasons)
        reasons.extend(commercial.errors)
        reasons.extend(commercial.warnings)
    decision = state.combined_decision
    if decision is not None and decision.status != "pass" and decision.summary:
        reasons.append(decision.summary)
    if not reasons:
        reasons.append("No exception requiring review was identified.")
    return tuple(dict.fromkeys(reason.strip() for reason in reasons if reason.strip()))


def _selected_product_description(
    state: QuotationWorkflowState,
    product_ids: list[str],
) -> str:
    recommendation = state.product_recommendation
    items = []
    if recommendation is not None:
        main_model = getattr(recommendation, "main_model", None)
        if main_model is not None:
            items.append(main_model)
        items.extend(getattr(recommendation, "accessories", ()) or ())
        items.extend(getattr(recommendation, "alternatives", ()) or ())
    selected = set(product_ids)
    descriptions = [
        str(getattr(item, "short_description", "")).strip()
        for item in items
        if str(getattr(item, "product_id", "")).strip() in selected
        and str(getattr(item, "short_description", "")).strip()
    ]
    if descriptions:
        return "; ".join(dict.fromkeys(descriptions))
    return state.draft.product_query.strip() or ", ".join(product_ids)


def _delivery_assumption(state: QuotationWorkflowState) -> str:
    requested_date = state.draft.requested_delivery_date
    if requested_date is not None:
        return (
            f"Requested delivery date: {requested_date.isoformat()}, "
            "subject to final confirmation."
        )
    return "Delivery schedule to be confirmed during final order processing."
