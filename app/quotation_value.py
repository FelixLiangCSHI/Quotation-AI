"""The single resolver for a quotation's commercial values.

A quotation is priced either as a legacy single-product quotation
(``state.pricing_result``) or as a multi-line quotation
(``state.quotation_pricing``). Approval, documents, emails and the Streamlit
UI must all read the price and currency the same way, so every caller uses
this module instead of reaching into one of the two pricing structures.

Only customer-visible commercial values are resolved here: currency, the
recommended unit price and the quotation revenue. Cost, margin and thresholds
stay with the deterministic pricing and margin-gate modules.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.quotation_models import QuotationWorkflowState

__all__ = [
    "QuotationCommercialValue",
    "resolve_commercial_value",
]


@dataclass(frozen=True)
class QuotationCommercialValue:
    """The commercial values every downstream consumer must agree on."""

    #: ``"single_product"``, ``"multi_line"`` or ``"unavailable"``.
    source: str
    currency: str
    quantity: int
    recommended_unit_price: float | None = None
    proposed_unit_price: float | None = None
    final_unit_price: float | None = None
    total_price: float | None = None

    @property
    def is_available(self) -> bool:
        return self.recommended_unit_price is not None


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number


def resolve_commercial_value(
    state: QuotationWorkflowState,
) -> QuotationCommercialValue:
    """Return the trusted commercial values for ``state``.

    The single-product pricing result wins when it carries a recommended
    price; otherwise the deterministic multi-line quotation revenue is used
    and divided by the draft quantity so a single unit price exists for the
    approval record. When neither is available the result is
    ``source="unavailable"`` and every price is ``None``.
    """

    quantity = max(int(state.draft.quantity or 1), 1)
    pricing = state.pricing_result
    analysis = state.quotation_pricing

    recommended: float | None = None
    source = "unavailable"
    currency = ""
    total_price: float | None = None

    if pricing is not None:
        currency = pricing.currency or currency
        recommended = _float_or_none(pricing.recommended_unit_price)
        if recommended is not None:
            source = "single_product"
            total_price = _float_or_none(pricing.total_price)

    if recommended is None and analysis is not None:
        revenue = _float_or_none(analysis.total_revenue)
        if revenue is not None and revenue > 0:
            source = "multi_line"
            currency = analysis.currency or currency
            recommended = revenue / quantity
            total_price = revenue

    if not currency:
        currency = state.draft.currency or "USD"

    proposed = _float_or_none(state.draft.proposed_unit_price)
    approved = _float_or_none(state.approval.final_price)
    final = approved if approved is not None else (
        proposed if proposed is not None else recommended
    )
    if total_price is None and final is not None:
        total_price = final * quantity

    return QuotationCommercialValue(
        source=source,
        currency=currency,
        quantity=quantity,
        recommended_unit_price=recommended,
        proposed_unit_price=proposed,
        final_unit_price=final,
        total_price=total_price,
    )
