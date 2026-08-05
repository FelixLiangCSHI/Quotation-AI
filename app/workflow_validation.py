from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.quotation_models import (
    ApprovalRecord,
    ApprovalStatus,
    QuotationWorkflowState,
    WorkflowStage,
    utc_now,
)
from app.workflow_state import append_audit_event


EDITABLE_FIELDS = frozenset(
    {
        "selected_product_ids",
        "quantity",
        "proposed_unit_price",
        "currency",
        "incoterm",
        "delivery_location",
    }
)


def apply_quotation_edits(
    state: QuotationWorkflowState,
    **edits: Any,
) -> tuple[str, ...]:
    unsupported = set(edits).difference(EDITABLE_FIELDS)
    if unsupported:
        fields = ", ".join(sorted(unsupported))
        raise ValueError(f"Unsupported quotation edits: {fields}")

    normalized_edits = _normalize_edits(edits)
    changed_fields: list[str] = []
    for field_name, value in normalized_edits.items():
        if getattr(state.draft, field_name) != value:
            setattr(state.draft, field_name, deepcopy(value))
            changed_fields.append(field_name)

    if not changed_fields:
        return ()

    before_approval_state = state.approval.status.value
    state.draft.updated_at = utc_now()
    state.draft.status = (
        WorkflowStage.READY_FOR_ANALYSIS
        if not state.draft.missing_fields and state.draft.selected_product_ids
        else WorkflowStage.COLLECTING_REQUIREMENTS
    )
    state.current_stage = state.draft.status
    invalidate_validation_outputs(state, clear_pricing=True)
    append_audit_event(
        state,
        (
            "price_edited"
            if "proposed_unit_price" in changed_fields
            else "field_updated"
        ),
        actor="user",
        before_state=before_approval_state,
        after_state=ApprovalStatus.NOT_READY.value,
        changed_fields=changed_fields,
        details={"changed_fields": changed_fields},
    )
    return tuple(changed_fields)


def invalidate_validation_outputs(
    state: QuotationWorkflowState,
    *,
    clear_pricing: bool,
) -> None:
    if clear_pricing:
        state.pricing_result = None
        state.quotation_pricing = None
    state.technical_validation = None
    state.commercial_validation = None
    state.combined_decision = None
    state.pricing_explanation = None
    state.internal_email = None
    state.customer_email = None
    state.validation_stale = True
    state.approval = ApprovalRecord(status=ApprovalStatus.NOT_READY)


def _normalize_edits(edits: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(edits)
    if "quantity" in normalized:
        quantity = int(normalized["quantity"])
        if quantity <= 0:
            raise ValueError("quantity must be greater than zero")
        normalized["quantity"] = quantity
    if "selected_product_ids" in normalized:
        normalized["selected_product_ids"] = [
            str(product_id).strip()
            for product_id in normalized["selected_product_ids"]
            if str(product_id).strip()
        ]
    for field_name in ("currency", "incoterm"):
        if field_name in normalized:
            normalized[field_name] = str(normalized[field_name]).strip().upper()
    if "delivery_location" in normalized:
        normalized["delivery_location"] = str(
            normalized["delivery_location"]
        ).strip()
    if "proposed_unit_price" in normalized:
        value = normalized["proposed_unit_price"]
        normalized["proposed_unit_price"] = (
            None if value is None else float(value)
        )
    return normalized
