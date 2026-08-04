"""Codec for persisting and restoring :class:`QuotationWorkflowState`.

The existing dataclass graph is reused verbatim so that every deterministic
engine (pricing, rules, validation, approval, documents) keeps operating on the
exact objects it does today. Only the transport changes: the state is written
to a JSON document and rebuilt from it.

The codec is deliberately tolerant when reading and strict when writing:
unknown keys are dropped rather than raising, so a state document written by a
newer schema version can still be opened by older code without corrupting it.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from typing import Any, get_args, get_origin

from app.quotation_models import (
    ApprovalRecord,
    ApprovalStatus,
    AuditEvent,
    CombinedDecision,
    CommercialRuleResult,
    CommercialValidationResult,
    ComparableQuotation,
    EmailOutput,
    LineItemCategory,
    QuotationLineItem,
    PricingResult,
    QuotationDraft,
    QuotationWorkflowState,
    TechnicalValidationResult,
    WorkflowStage,
)

#: Bumped whenever the persisted shape changes incompatibly.
STATE_SCHEMA_VERSION = 1


class WorkflowStateCodecError(ValueError):
    """Raised when a stored state document cannot be restored."""


def dump_workflow_state(state: QuotationWorkflowState) -> dict[str, Any]:
    """Serialise a workflow state into a JSON-compatible document."""

    document = state.to_dict()
    document["schema_version"] = STATE_SCHEMA_VERSION
    return document


def load_workflow_state(document: dict[str, Any]) -> QuotationWorkflowState:
    """Rebuild a workflow state from a stored document."""

    if not isinstance(document, dict):
        raise WorkflowStateCodecError("State document must be a mapping.")

    payload = {key: value for key, value in document.items()}
    payload.pop("schema_version", None)

    draft_payload = payload.get("draft")
    if not isinstance(draft_payload, dict):
        raise WorkflowStateCodecError("State document is missing a draft.")

    state = QuotationWorkflowState(draft=_build(QuotationDraft, draft_payload))

    state.pricing_result = _optional(PricingResult, payload.get("pricing_result"))
    state.technical_validation = _optional(
        TechnicalValidationResult, payload.get("technical_validation")
    )
    state.commercial_validation = _optional(
        CommercialValidationResult, payload.get("commercial_validation")
    )
    state.combined_decision = _optional(
        CombinedDecision, payload.get("combined_decision")
    )
    state.internal_email = _optional(EmailOutput, payload.get("internal_email"))
    state.customer_email = _optional(EmailOutput, payload.get("customer_email"))

    approval_payload = payload.get("approval")
    if isinstance(approval_payload, dict):
        state.approval = _build(ApprovalRecord, approval_payload)

    events = payload.get("audit_events") or []
    if not isinstance(events, list):
        raise WorkflowStateCodecError("audit_events must be a list.")
    state.audit_events = [
        _build(AuditEvent, item) for item in events if isinstance(item, dict)
    ]

    state.validation_stale = bool(payload.get("validation_stale", True))
    stage = payload.get("current_stage") or WorkflowStage.DRAFT.value
    try:
        state.current_stage = WorkflowStage(stage)
    except ValueError as error:
        raise WorkflowStateCodecError(f"Unknown workflow stage: {stage!r}") from error

    # The product recommendation is a rendered advisory object rather than
    # trusted commercial state. It is intentionally not restored; the
    # recommender is re-run on demand.
    state.product_recommendation = None
    return state


def _optional(target: type, payload: Any) -> Any | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise WorkflowStateCodecError(
            f"Expected a mapping for {target.__name__}, got {type(payload).__name__}"
        )
    return _build(target, payload)


def _build(target: type, payload: dict[str, Any]) -> Any:
    """Instantiate a dataclass from a payload, coercing known field types."""

    if not is_dataclass(target):
        raise WorkflowStateCodecError(f"{target.__name__} is not a dataclass")

    kwargs: dict[str, Any] = {}
    for item in fields(target):
        if item.name not in payload:
            continue
        kwargs[item.name] = _coerce(item.type, payload[item.name])
    try:
        return target(**kwargs)
    except TypeError as error:
        raise WorkflowStateCodecError(
            f"Cannot restore {target.__name__}: {error}"
        ) from error


#: Nested dataclasses that may appear inside a state document.
_NESTED_TYPES: dict[str, type] = {
    "ComparableQuotation": ComparableQuotation,
    "CommercialRuleResult": CommercialRuleResult,
    "QuotationLineItem": QuotationLineItem,
}

_ENUM_TYPES: dict[str, type] = {
    "WorkflowStage": WorkflowStage,
    "ApprovalStatus": ApprovalStatus,
    "LineItemCategory": LineItemCategory,
}


def _coerce(annotation: Any, value: Any) -> Any:
    """Coerce a JSON value back into the type the dataclass field expects.

    Annotations are compared as strings because ``from __future__ import
    annotations`` is active in the models module, so field types arrive
    unevaluated.
    """

    if value is None:
        return None

    text = annotation if isinstance(annotation, str) else _annotation_text(annotation)

    for name, enum_type in _ENUM_TYPES.items():
        if text.startswith(name):
            return enum_type(value)

    if text.startswith("datetime"):
        return _parse_datetime(value)
    if text.startswith("date "):
        return _parse_date(value)
    if text == "date" or text.startswith("date |"):
        return _parse_date(value)

    for name, nested in _NESTED_TYPES.items():
        if name in text and isinstance(value, list):
            return [
                _build(nested, item) if isinstance(item, dict) else item
                for item in value
            ]

    return value


def _annotation_text(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", str(annotation))
    args = ", ".join(_annotation_text(arg) for arg in get_args(annotation))
    return f"{getattr(origin, '__name__', str(origin))}[{args}]"


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as error:
        raise WorkflowStateCodecError(
            f"Invalid datetime value: {value!r}"
        ) from error


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise WorkflowStateCodecError(f"Invalid date value: {value!r}") from error
