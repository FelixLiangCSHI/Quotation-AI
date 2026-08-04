"""Multi-line quotation composition.

A quotation is a collection of line items: one or more main products plus
accessories, installation, warranty, service and optional commercial
additions. A single-product quotation is a one-element collection, so all
existing single-item behaviour is preserved.

Every product line is checked deterministically by the rule engine *before* it
can be added. An incompatible product is rejected outright; a line item is
never added on the strength of an AI suggestion alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence
from uuid import uuid4

from app.quotation_models import (
    LineItemCategory,
    QuotationDraft,
    QuotationLineItem,
    QuotationWorkflowState,
    RecommendationStatus,
    WorkflowStage,
    utc_now,
)
from app.recommender import QuoteRecommendation
from app.rule_engine import QuotationRuleEngine
from app.workflow_state import append_audit_event
from app.workflow_validation import invalidate_validation_outputs


#: Categories that describe a physical, rule-checked product.
PRODUCT_CATEGORIES = frozenset(
    {LineItemCategory.MAIN_PRODUCT, LineItemCategory.ACCESSORY}
)

#: Categories that carry no catalogue product and therefore cannot be
#: rule-checked. They are reported as ``not_evaluated``.
NON_PRODUCT_CATEGORIES = frozenset(
    {
        LineItemCategory.INSTALLATION,
        LineItemCategory.WARRANTY,
        LineItemCategory.SERVICE,
        LineItemCategory.COMMERCIAL_ADDITION,
    }
)


class LineItemError(ValueError):
    """Raised when a line item cannot be added or edited."""


@dataclass(frozen=True)
class CompatibilityCheck:
    """Deterministic verdict for a candidate line item."""

    status: RecommendationStatus
    reasons: tuple[str, ...] = ()

    @property
    def is_addable(self) -> bool:
        return self.status is not RecommendationStatus.INCOMPATIBLE


@dataclass(frozen=True)
class RecommendedLine:
    """A recommendation offered to the user, with its evaluated status."""

    product_id: str
    description: str
    category: LineItemCategory
    status: RecommendationStatus
    reason: str = ""
    quantity: int = 1


def new_line_id() -> str:
    return f"LI-{uuid4().hex[:8].upper()}"


def check_line_item_compatibility(
    draft: QuotationDraft,
    *,
    product_id: str,
    category: LineItemCategory,
    engine: QuotationRuleEngine,
) -> CompatibilityCheck:
    """Evaluate whether ``product_id`` may join the current configuration."""

    category = LineItemCategory(category)
    if category in NON_PRODUCT_CATEGORIES:
        return CompatibilityCheck(
            status=RecommendationStatus.NOT_EVALUATED,
            reasons=(
                "Service and commercial lines are not evaluated by the "
                "product rule engine.",
            ),
        )

    normalized_id = product_id.strip()
    if not normalized_id:
        return CompatibilityCheck(
            status=RecommendationStatus.INCOMPATIBLE,
            reasons=("A product line item requires a product id.",),
        )

    if not draft.region:
        return CompatibilityCheck(
            status=RecommendationStatus.NOT_EVALUATED,
            reasons=("The region is unknown, so region rules cannot be checked.",),
        )

    existing_ids = [
        item.product_id
        for item in draft.line_items
        if item.product_id and item.category in PRODUCT_CATEGORIES
    ]
    candidate_ids = list(dict.fromkeys([*existing_ids, normalized_id]))
    result = engine.check_configuration(candidate_ids, region=draft.region)
    blocking = tuple(
        issue.message
        for issue in result.issues
        if issue.severity == "error"
        and (issue.product_id is None or issue.product_id == normalized_id)
    )
    if blocking:
        return CompatibilityCheck(
            status=RecommendationStatus.INCOMPATIBLE,
            reasons=blocking,
        )

    warnings = tuple(
        issue.message for issue in result.issues if issue.severity != "error"
    )
    if result.status == "incomplete":
        return CompatibilityCheck(
            status=RecommendationStatus.NOT_EVALUATED,
            reasons=warnings
            or ("Not enough information to complete every rule check.",),
        )
    status = (
        RecommendationStatus.REQUIRED
        if category is LineItemCategory.MAIN_PRODUCT
        else RecommendationStatus.RECOMMENDED
    )
    return CompatibilityCheck(status=status, reasons=warnings)


def add_line_item(
    state: QuotationWorkflowState,
    *,
    product_id: str = "",
    description: str = "",
    category: LineItemCategory | str = LineItemCategory.ACCESSORY,
    quantity: int = 1,
    unit_price: float | None = None,
    is_optional: bool = False,
    source: str = "manual",
    engine: QuotationRuleEngine,
    actor: str = "user",
) -> QuotationLineItem:
    """Add a line item after a deterministic compatibility check."""

    category = LineItemCategory(category)
    quantity = _validate_quantity(quantity)
    check = check_line_item_compatibility(
        state.draft,
        product_id=product_id,
        category=category,
        engine=engine,
    )
    if not check.is_addable:
        raise LineItemError(
            "Cannot add this line item: " + "; ".join(check.reasons)
        )

    normalized_id = product_id.strip()
    if normalized_id and any(
        item.product_id == normalized_id and item.category is category
        for item in state.draft.line_items
    ):
        raise LineItemError(
            f"{normalized_id} is already on the quotation as a "
            f"{category.value.replace('_', ' ')}."
        )

    item = QuotationLineItem(
        line_id=new_line_id(),
        product_id=normalized_id,
        description=description.strip() or normalized_id,
        category=category,
        quantity=quantity,
        unit_price=unit_price,
        is_optional=is_optional,
        source=source,
        notes="; ".join(check.reasons),
    )
    state.draft.line_items.append(item)
    _apply_material_change(
        state,
        actor=actor,
        event_type="line_item_added",
        changed_fields=("line_items",),
        details={
            "line_id": item.line_id,
            "product_id": item.product_id,
            "category": item.category.value,
            "quantity": item.quantity,
            "compatibility": check.status.value,
        },
    )
    return item


def update_line_item(
    state: QuotationWorkflowState,
    line_id: str,
    *,
    quantity: int | None = None,
    unit_price: float | None = None,
    description: str | None = None,
    is_optional: bool | None = None,
    actor: str = "user",
) -> QuotationLineItem:
    """Edit one line item. Any accepted edit is treated as material."""

    item = find_line_item(state.draft, line_id)
    changed: list[str] = []
    if quantity is not None:
        new_quantity = _validate_quantity(quantity)
        if new_quantity != item.quantity:
            item.quantity = new_quantity
            changed.append("quantity")
    if unit_price is not None and unit_price != item.unit_price:
        if unit_price < 0:
            raise LineItemError("unit price cannot be negative")
        item.unit_price = float(unit_price)
        changed.append("unit_price")
    if description is not None and description.strip() != item.description:
        item.description = description.strip()
        changed.append("description")
    if is_optional is not None and bool(is_optional) != item.is_optional:
        item.is_optional = bool(is_optional)
        changed.append("is_optional")

    if not changed:
        return item

    _apply_material_change(
        state,
        actor=actor,
        event_type="line_item_updated",
        changed_fields=("line_items", *changed),
        details={"line_id": line_id, "changed_fields": changed},
    )
    return item


def remove_line_item(
    state: QuotationWorkflowState,
    line_id: str,
    *,
    actor: str = "user",
) -> QuotationLineItem:
    item = find_line_item(state.draft, line_id)
    state.draft.line_items = [
        entry for entry in state.draft.line_items if entry.line_id != line_id
    ]
    _apply_material_change(
        state,
        actor=actor,
        event_type="line_item_removed",
        changed_fields=("line_items",),
        details={"line_id": line_id, "product_id": item.product_id},
    )
    return item


def find_line_item(draft: QuotationDraft, line_id: str) -> QuotationLineItem:
    for item in draft.line_items:
        if item.line_id == line_id:
            return item
    raise LineItemError(f"Unknown line item: {line_id}")


def line_items_by_category(
    draft: QuotationDraft,
    category: LineItemCategory | str,
) -> tuple[QuotationLineItem, ...]:
    wanted = LineItemCategory(category)
    return tuple(item for item in draft.line_items if item.category is wanted)


def quotation_total(draft: QuotationDraft, *, include_optional: bool = False) -> float:
    total = 0.0
    for item in draft.line_items:
        if item.is_optional and not include_optional:
            continue
        extended = item.extended_price
        if extended is not None:
            total += extended
    return round(total, 2)


def build_recommendations(
    draft: QuotationDraft,
    recommendation: QuoteRecommendation | None,
    engine: QuotationRuleEngine,
) -> tuple[RecommendedLine, ...]:
    """Turn a catalogue recommendation into status-labelled proposals."""

    if recommendation is None:
        return ()

    existing = {item.product_id for item in draft.line_items if item.product_id}
    lines: list[RecommendedLine] = []

    def _append(item, category: LineItemCategory, default_status=None) -> None:
        if item is None or item.product_id in existing:
            return
        check = check_line_item_compatibility(
            draft,
            product_id=item.product_id,
            category=category,
            engine=engine,
        )
        status = check.status
        if (
            default_status is not None
            and status
            not in {
                RecommendationStatus.INCOMPATIBLE,
                RecommendationStatus.NOT_EVALUATED,
            }
        ):
            status = default_status
        lines.append(
            RecommendedLine(
                product_id=item.product_id,
                description=item.short_description,
                category=category,
                status=status,
                reason=item.reason,
                quantity=max(int(getattr(item, "quantity", 1) or 1), 1),
            )
        )

    _append(
        recommendation.main_model,
        LineItemCategory.MAIN_PRODUCT,
        RecommendationStatus.REQUIRED,
    )
    for accessory in recommendation.accessories:
        _append(
            accessory,
            LineItemCategory.ACCESSORY,
            RecommendationStatus.RECOMMENDED,
        )
    for alternative in recommendation.alternatives:
        _append(
            alternative,
            LineItemCategory.ACCESSORY,
            RecommendationStatus.OPTIONAL,
        )
    return tuple(lines)


def sync_selected_product_ids(draft: QuotationDraft) -> list[str]:
    """Keep the legacy single-product field in step with the line items.

    The main product lines drive ``selected_product_ids`` so that the existing
    pricing, validation and document code keeps working unchanged.
    """

    main_ids = [
        item.product_id
        for item in draft.line_items
        if item.category is LineItemCategory.MAIN_PRODUCT and item.product_id
    ]
    if main_ids:
        draft.selected_product_ids = list(dict.fromkeys(main_ids))
    return draft.selected_product_ids


def add_recommended_lines(
    state: QuotationWorkflowState,
    lines: Iterable[RecommendedLine],
    *,
    engine: QuotationRuleEngine,
    accept_statuses: Sequence[RecommendationStatus] = (
        RecommendationStatus.REQUIRED,
        RecommendationStatus.RECOMMENDED,
    ),
    actor: str = "user",
) -> tuple[QuotationLineItem, ...]:
    """Add every recommendation whose status is in ``accept_statuses``."""

    accepted = set(accept_statuses)
    added: list[QuotationLineItem] = []
    for line in lines:
        if line.status not in accepted:
            continue
        added.append(
            add_line_item(
                state,
                product_id=line.product_id,
                description=line.description,
                category=line.category,
                quantity=line.quantity,
                source="recommendation",
                engine=engine,
                actor=actor,
            )
        )
    return tuple(added)


def _validate_quantity(quantity: int) -> int:
    try:
        value = int(quantity)
    except (TypeError, ValueError) as error:
        raise LineItemError("quantity must be a whole number") from error
    if value < 1:
        raise LineItemError("quantity must be at least 1")
    if value > 999:
        raise LineItemError("quantity must be 999 or fewer units")
    return value


def _apply_material_change(
    state: QuotationWorkflowState,
    *,
    actor: str,
    event_type: str,
    changed_fields: tuple[str, ...],
    details: dict,
) -> None:
    """Record a material edit and clear every downstream derived result."""

    before_approval_state = state.approval.status.value
    sync_selected_product_ids(state.draft)
    state.draft.updated_at = utc_now()
    invalidate_validation_outputs(state, clear_pricing=True)
    state.draft.status = (
        WorkflowStage.READY_FOR_ANALYSIS
        if not state.draft.missing_fields and state.draft.selected_product_ids
        else WorkflowStage.COLLECTING_REQUIREMENTS
    )
    state.current_stage = state.draft.status
    append_audit_event(
        state,
        event_type,
        actor=actor,
        before_state=before_approval_state,
        after_state=state.approval.status.value,
        changed_fields=list(changed_fields),
        details=details,
    )
