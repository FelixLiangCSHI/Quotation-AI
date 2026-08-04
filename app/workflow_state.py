from __future__ import annotations

from collections.abc import MutableMapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.config import REQUIRED_QUOTATION_FIELDS
from app.quotation_models import (
    ApprovalRecord,
    ApprovalStatus,
    AuditEvent,
    QuotationDraft,
    QuotationWorkflowState,
    utc_now,
)


WORKFLOW_STATE_KEY = "quotation_workflow"


def generate_quotation_id() -> str:
    return f"Q-{utc_now():%Y%m%d}-{uuid4().hex[:10].upper()}"


def initialize_workflow_state(
    *,
    quotation_id: str | None = None,
    timestamp: datetime | None = None,
) -> QuotationWorkflowState:
    created_at = timestamp or utc_now()
    draft = QuotationDraft(
        quotation_id=quotation_id or generate_quotation_id(),
        missing_fields=list(REQUIRED_QUOTATION_FIELDS),
        created_at=created_at,
        updated_at=created_at,
    )
    state = QuotationWorkflowState(draft=draft)
    append_audit_event(
        state,
        "draft_created",
        actor="system",
        before_state="",
        after_state=ApprovalStatus.NOT_READY.value,
        timestamp=created_at,
    )
    return state


def get_or_initialize_workflow_state(
    session_state: MutableMapping[str, Any],
    *,
    key: str = WORKFLOW_STATE_KEY,
) -> QuotationWorkflowState:
    if key not in session_state:
        session_state[key] = initialize_workflow_state()

    state = session_state[key]
    if not isinstance(state, QuotationWorkflowState):
        raise TypeError(f"Session value {key!r} is not a QuotationWorkflowState")
    return state


def append_audit_event(
    state: QuotationWorkflowState,
    event_type: str,
    *,
    actor: str = "system",
    details: dict[str, Any] | None = None,
    before_state: str = "",
    after_state: str = "",
    changed_fields: list[str] | tuple[str, ...] | None = None,
    reason: str = "",
    triggered_rule_ids: list[str] | tuple[str, ...] | None = None,
    timestamp: datetime | None = None,
) -> AuditEvent:
    normalized_event_type = event_type.strip()
    normalized_actor = actor.strip()
    if not normalized_event_type:
        raise ValueError("event_type cannot be blank")
    if not normalized_actor:
        raise ValueError("actor cannot be blank")

    event_time = timestamp or utc_now()
    event = AuditEvent(
        event_type=normalized_event_type,
        actor=normalized_actor,
        timestamp=event_time,
        quotation_id=state.draft.quotation_id,
        before_state=before_state,
        after_state=after_state,
        changed_fields=list(
            changed_fields
            if changed_fields is not None
            else (details or {}).get("changed_fields", [])
        ),
        reason=reason,
        triggered_rule_ids=list(triggered_rule_ids or ()),
        details=dict(details or {}),
    )
    state.audit_events.append(event)
    state.draft.updated_at = event_time
    return event


def reset_workflow_state(
    session_state: MutableMapping[str, Any],
    *,
    key: str = WORKFLOW_STATE_KEY,
) -> QuotationWorkflowState:
    previous = session_state.get(key)
    previous_events: list[AuditEvent] = []
    if isinstance(previous, QuotationWorkflowState):
        append_audit_event(
            previous,
            "quotation_reset",
            actor="user",
            before_state=previous.approval.status.value,
            after_state=ApprovalStatus.NOT_READY.value,
        )
        previous_events = list(previous.audit_events)
    state = initialize_workflow_state()
    if previous_events:
        state.audit_events = [*previous_events, *state.audit_events]
    session_state[key] = state
    return state
