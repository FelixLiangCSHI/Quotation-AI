"""Trusted customer document context.

Every value in :class:`CustomerDocumentContext` is copied from the approved,
persisted quotation state. Agent 4 can never create or alter any of them: the
document plan carries narrative text only.

The context is deliberately customer-safe by construction. Estimated costs,
gross margin, the policy threshold, policy versions, rule identifiers,
override justifications, comparable prices, workbook paths and data-source
cells are simply never read into it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.config import DEMO_QUOTATION_VALIDITY_DAYS
from app.quotation_models import (
    ApprovalStatus,
    LineItemCategory,
    QuotationWorkflowState,
    utc_now,
)


#: Only these approval statuses may produce a customer quotation document.
APPROVED_STATUSES = frozenset(
    {ApprovalStatus.APPROVED, ApprovalStatus.APPROVED_WITH_OVERRIDE}
)

#: Human-readable customer categories. Internal category codes are not shown.
CATEGORY_LABELS = {
    LineItemCategory.MAIN_PRODUCT: "Product",
    LineItemCategory.ACCESSORY: "Accessory",
    LineItemCategory.INSTALLATION: "Installation",
    LineItemCategory.WARRANTY: "Warranty",
    LineItemCategory.SERVICE: "Service",
    LineItemCategory.COMMERCIAL_ADDITION: "Commercial item",
}


class DocumentContextError(ValueError):
    """Raised when a customer document must not be produced."""


@dataclass(frozen=True)
class CustomerLineItem:
    """One approved, customer-visible quotation line."""

    position: int
    product_id: str
    description: str
    category_label: str
    quantity: int
    unit_price: Decimal
    extended_price: Decimal
    is_optional: bool = False


@dataclass(frozen=True)
class CustomerDocumentContext:
    """Approved, customer-safe facts. The single source of document truth."""

    quotation_id: str
    quotation_version: int
    customer_name: str
    currency: str
    quotation_date: date
    validity_date: date
    incoterm: str
    delivery_location: str
    delivery_assumption: str
    line_items: tuple[CustomerLineItem, ...]
    subtotal: Decimal
    total: Decimal
    approval_status: str
    document_version: str = "1"
    region: str = ""
    optional_total: Decimal = Decimal("0.00")

    @property
    def has_optional_items(self) -> bool:
        return any(item.is_optional for item in self.line_items)

    def protected_values(self) -> tuple[str, ...]:
        """Values Agent 4 must never contradict; used for plan screening."""

        values = [
            self.quotation_id,
            str(self.quotation_version),
            self.customer_name,
            self.currency,
            self.incoterm,
            self.quotation_date.isoformat(),
            self.validity_date.isoformat(),
            _money_text(self.total),
        ]
        for item in self.line_items:
            values.extend(
                [
                    item.product_id,
                    str(item.quantity),
                    _money_text(item.unit_price),
                    _money_text(item.extended_price),
                ]
            )
        return tuple(dict.fromkeys(value for value in values if value))

    def category_composition(self) -> tuple[tuple[str, Decimal], ...]:
        """Revenue by customer-visible category. Never cost or margin."""

        totals: dict[str, Decimal] = {}
        for item in self.line_items:
            totals[item.category_label] = (
                totals.get(item.category_label, Decimal("0")) + item.extended_price
            )
        return tuple(sorted(totals.items(), key=lambda pair: pair[0]))

    def quantity_breakdown(self) -> tuple[tuple[str, int], ...]:
        totals: dict[str, int] = {}
        for item in self.line_items:
            totals[item.category_label] = (
                totals.get(item.category_label, 0) + item.quantity
            )
        return tuple(sorted(totals.items(), key=lambda pair: pair[0]))


def build_customer_document_context(
    state: QuotationWorkflowState,
    *,
    quotation_version: int = 1,
    as_of: date | None = None,
    validity_days: int = DEMO_QUOTATION_VALIDITY_DAYS,
    document_version: str = "1",
) -> CustomerDocumentContext:
    """Build the trusted context, or refuse to build one at all.

    A customer document exists only for an approved quotation whose current
    validation is not stale.
    """

    approval = state.approval
    if approval.status not in APPROVED_STATUSES:
        raise DocumentContextError(
            "A customer quotation document requires an approved quotation."
        )
    if state.validation_stale or state.combined_decision is None:
        raise DocumentContextError(
            "A customer quotation document requires current validation."
        )

    draft = state.draft
    currency = _currency(state)
    items = _line_items(state, currency)
    if not items:
        raise DocumentContextError(
            "A customer quotation document requires at least one priced line."
        )

    subtotal = sum(
        (item.extended_price for item in items if not item.is_optional),
        Decimal("0"),
    )
    optional_total = sum(
        (item.extended_price for item in items if item.is_optional),
        Decimal("0"),
    )
    if subtotal <= 0:
        raise DocumentContextError(
            "The approved quotation total must be greater than zero."
        )

    quotation_date = as_of or (
        approval.timestamp.date()
        if approval.timestamp is not None
        else utc_now().date()
    )
    return CustomerDocumentContext(
        quotation_id=draft.quotation_id,
        quotation_version=int(quotation_version or 1),
        customer_name=draft.customer_name or "Customer",
        currency=currency,
        quotation_date=quotation_date,
        validity_date=quotation_date + timedelta(days=int(validity_days)),
        incoterm=draft.incoterm or "To be confirmed",
        delivery_location=draft.delivery_location or "To be confirmed",
        delivery_assumption=_delivery_assumption(state),
        line_items=items,
        subtotal=_money(subtotal),
        total=_money(subtotal),
        optional_total=_money(optional_total),
        approval_status=approval.status.value,
        document_version=str(document_version or "1"),
        region=draft.region or "",
    )


def _currency(state: QuotationWorkflowState) -> str:
    pricing = state.quotation_pricing
    if pricing is not None and pricing.currency:
        return pricing.currency
    result = state.pricing_result
    if result is not None and getattr(result, "currency", ""):
        return result.currency
    return state.draft.currency or "USD"


def _line_items(
    state: QuotationWorkflowState, currency: str
) -> tuple[CustomerLineItem, ...]:
    draft = state.draft
    if draft.line_items:
        items = []
        for position, line in enumerate(draft.line_items, start=1):
            unit_price = _approved_unit_price(line)
            if unit_price is None:
                raise DocumentContextError(
                    "Every quotation line must carry an approved unit price."
                )
            quantity = int(line.quantity)
            items.append(
                CustomerLineItem(
                    position=position,
                    product_id=line.product_id,
                    description=line.description or line.product_id,
                    category_label=CATEGORY_LABELS.get(
                        line.category, "Item"
                    ),
                    quantity=quantity,
                    unit_price=_money(unit_price),
                    extended_price=_money(unit_price * Decimal(quantity)),
                    is_optional=bool(line.is_optional),
                )
            )
        return tuple(items)
    return _single_line_items(state, currency)


def _single_line_items(
    state: QuotationWorkflowState, currency: str
) -> tuple[CustomerLineItem, ...]:
    """Backwards-compatible one-element collection for legacy quotations."""

    pricing = state.pricing_result
    if pricing is None or pricing.recommended_unit_price is None:
        raise DocumentContextError(
            "Current pricing is required before generating a customer document."
        )
    approval = state.approval
    unit_price = (
        _decimal(approval.final_price)
        or _decimal(state.draft.proposed_unit_price)
        or _decimal(pricing.recommended_unit_price)
    )
    if unit_price is None or unit_price <= 0:
        raise DocumentContextError(
            "The approved unit price must be greater than zero."
        )
    quantity = int(state.draft.quantity)
    if quantity <= 0:
        raise DocumentContextError("Quotation quantity must be greater than zero.")
    product_ids = state.draft.selected_product_ids or list(
        getattr(pricing, "selected_product_ids", ()) or ()
    )
    product_id = ", ".join(product_ids) if product_ids else "Configured product"
    return (
        CustomerLineItem(
            position=1,
            product_id=product_id,
            description=state.draft.product_query.strip() or product_id,
            category_label=CATEGORY_LABELS[LineItemCategory.MAIN_PRODUCT],
            quantity=quantity,
            unit_price=_money(unit_price),
            extended_price=_money(unit_price * Decimal(quantity)),
        ),
    )


def _approved_unit_price(line) -> Decimal | None:
    for candidate in (
        line.approved_unit_price,
        line.unit_price,
        line.recommended_unit_price,
    ):
        value = _decimal(candidate)
        if value is not None and value > 0:
            return value
    return None


def _decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_text(value: Decimal) -> str:
    return f"{_money(value):,.2f}"


def format_money(value: Decimal, currency: str) -> str:
    return f"{currency} {_money(value):,.2f}"


def _delivery_assumption(state: QuotationWorkflowState) -> str:
    requested = state.draft.requested_delivery_date
    if requested is not None:
        return (
            f"Requested delivery date {requested.isoformat()}, subject to final "
            "order confirmation and scheduling."
        )
    return "Delivery schedule to be confirmed during final order processing."
