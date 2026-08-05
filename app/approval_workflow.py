from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from app.quotation_models import (
    ApprovalRecord,
    ApprovalStatus,
    QuotationWorkflowState,
    WorkflowStage,
    utc_now,
)
from app.quotation_value import resolve_commercial_value
from app.workflow_state import append_audit_event


APPROVER_ROLES = (
    "Sales Representative",
    "Sales Manager",
    "Pricing Manager",
    "Demo Approver",
)

ACTION_APPROVE = "approve"
ACTION_APPROVE_WITH_OVERRIDE = "approve_with_override"
ACTION_REQUEST_REVISION = "request_revision"
ACTION_REJECT = "reject"

ALLOWED_ACTIONS_BY_DECISION = {
    "pass": (ACTION_APPROVE,),
    "pass_with_warnings": (ACTION_APPROVE, ACTION_REQUEST_REVISION),
    "review_required": (
        ACTION_APPROVE_WITH_OVERRIDE,
        ACTION_REQUEST_REVISION,
        ACTION_REJECT,
    ),
    "blocked": (ACTION_REQUEST_REVISION, ACTION_REJECT),
}


class ApprovalWorkflowError(ValueError):
    pass


class InvalidApprovalTransitionError(ApprovalWorkflowError):
    pass


class DuplicateApprovalActionError(ApprovalWorkflowError):
    pass


def prepare_approval(state: QuotationWorkflowState) -> ApprovalRecord:
    if state.approval.status in {
        ApprovalStatus.APPROVED,
        ApprovalStatus.APPROVED_WITH_OVERRIDE,
        ApprovalStatus.REJECTED,
        ApprovalStatus.REVISION_REQUESTED,
    }:
        return state.approval

    if (
        state.validation_stale
        or state.combined_decision is None
        or (state.pricing_result is None and state.quotation_pricing is None)
    ):
        state.approval = ApprovalRecord(status=ApprovalStatus.NOT_READY)
        return state.approval

    if state.approval.status != ApprovalStatus.PENDING_REVIEW:
        state.approval = ApprovalRecord(
            status=ApprovalStatus.PENDING_REVIEW,
            reminder_due_at=approval_reminder_due_at(state),
        )
    return state.approval


def _approvable_unit_price(state: QuotationWorkflowState) -> float | None:
    """The trusted unit price an approval decision is recorded against.

    A legacy single-product quotation uses the recommended price. A multi-line
    quotation has no single recommended price, so the deterministic quotation
    revenue per unit is used instead. Cost and margin are never involved.
    """

    return resolve_commercial_value(state).recommended_unit_price


def available_approval_actions(
    state: QuotationWorkflowState,
) -> tuple[str, ...]:
    approval = prepare_approval(state)
    if (
        approval.status != ApprovalStatus.PENDING_REVIEW
        or state.combined_decision is None
    ):
        return ()
    return ALLOWED_ACTIONS_BY_DECISION.get(state.combined_decision.status, ())


def submit_approval_action(
    state: QuotationWorkflowState,
    *,
    action: str,
    actor_role: str,
    actor_name: str = "",
    reason: str = "",
    final_unit_price: float | None = None,
    action_id: str | None = None,
    timestamp: datetime | None = None,
) -> ApprovalRecord:
    normalized_action = action.strip().casefold()
    normalized_role = actor_role.strip()
    normalized_name = actor_name.strip()
    normalized_reason = reason.strip()
    request_id = action_id or uuid4().hex

    if normalized_role not in APPROVER_ROLES:
        raise ApprovalWorkflowError("A valid approver role is required.")
    if state.approval.action_id and state.approval.action_id == request_id:
        raise DuplicateApprovalActionError(
            "This approval action has already been processed."
        )

    approval = prepare_approval(state)
    if approval.status != ApprovalStatus.PENDING_REVIEW:
        if approval.status in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.APPROVED_WITH_OVERRIDE,
            ApprovalStatus.REJECTED,
            ApprovalStatus.REVISION_REQUESTED,
        }:
            raise DuplicateApprovalActionError(
                "This quotation already has a completed approval action."
            )
        raise InvalidApprovalTransitionError(
            "The quotation is not ready for approval review."
        )

    allowed_actions = available_approval_actions(state)
    if normalized_action not in allowed_actions:
        raise InvalidApprovalTransitionError(
            f"Action {normalized_action!r} is not allowed for decision "
            f"{state.combined_decision.status!r}."
        )

    if normalized_action in {
        ACTION_APPROVE_WITH_OVERRIDE,
        ACTION_REQUEST_REVISION,
        ACTION_REJECT,
    } and not normalized_reason:
        raise ApprovalWorkflowError(
            "A reason is required for override, revision, or rejection."
        )

    recommended_price = _approvable_unit_price(state)
    if recommended_price is None:
        raise InvalidApprovalTransitionError(
            "A recommended price is required before approval."
        )
    final_price = (
        float(final_unit_price)
        if final_unit_price is not None
        else float(recommended_price)
    )
    if normalized_action in {ACTION_APPROVE, ACTION_APPROVE_WITH_OVERRIDE}:
        if final_price <= 0:
            raise ApprovalWorkflowError(
                "The final approved unit price must be greater than zero."
            )
    if (
        normalized_action == ACTION_APPROVE
        and abs(final_price - float(recommended_price)) >= 0.005
    ):
        raise InvalidApprovalTransitionError(
            "A changed final price must be edited, revalidated, or approved "
            "through an allowed override action."
        )

    status_by_action = {
        ACTION_APPROVE: ApprovalStatus.APPROVED,
        ACTION_APPROVE_WITH_OVERRIDE: ApprovalStatus.APPROVED_WITH_OVERRIDE,
        ACTION_REQUEST_REVISION: ApprovalStatus.REVISION_REQUESTED,
        ACTION_REJECT: ApprovalStatus.REJECTED,
    }
    event_by_action = {
        ACTION_APPROVE: "approval_granted",
        ACTION_APPROVE_WITH_OVERRIDE: "override_granted",
        ACTION_REQUEST_REVISION: "revision_requested",
        ACTION_REJECT: "quotation_rejected",
    }
    new_status = status_by_action[normalized_action]
    actor = normalized_name or normalized_role
    event_time = timestamp or utc_now()
    triggered_rules = list(state.combined_decision.triggered_rule_ids)
    state.approval = ApprovalRecord(
        status=new_status,
        actor=actor,
        actor_role=normalized_role,
        action=normalized_action,
        reason=normalized_reason,
        original_price=float(recommended_price),
        final_price=(
            final_price
            if normalized_action
            in {ACTION_APPROVE, ACTION_APPROVE_WITH_OVERRIDE}
            else None
        ),
        timestamp=event_time,
        triggered_rule_ids=triggered_rules,
        override_justification=(
            normalized_reason
            if normalized_action == ACTION_APPROVE_WITH_OVERRIDE
            else ""
        ),
        action_id=request_id,
        reminder_due_at=approval.reminder_due_at,
    )
    state.current_stage = {
        ApprovalStatus.APPROVED: WorkflowStage.APPROVED,
        ApprovalStatus.APPROVED_WITH_OVERRIDE: WorkflowStage.APPROVED,
        ApprovalStatus.REJECTED: WorkflowStage.REJECTED,
        ApprovalStatus.REVISION_REQUESTED: WorkflowStage.REVIEW_REQUIRED,
    }[new_status]
    append_audit_event(
        state,
        event_by_action[normalized_action],
        actor=actor,
        before_state=ApprovalStatus.PENDING_REVIEW.value,
        after_state=new_status.value,
        changed_fields=(
            ["final_unit_price"]
            if normalized_action == ACTION_APPROVE_WITH_OVERRIDE
            else []
        ),
        reason=normalized_reason,
        triggered_rule_ids=triggered_rules,
        timestamp=event_time,
    )
    return state.approval


def approval_reminder_due_at(state: QuotationWorkflowState) -> datetime:
    return state.draft.created_at + timedelta(days=2)


def approval_reminder_status(
    state: QuotationWorkflowState,
    *,
    now: datetime | None = None,
) -> str:
    approval = prepare_approval(state)
    if approval.status != ApprovalStatus.PENDING_REVIEW:
        return "No reminder is pending."
    current_time = now or utc_now()
    due_at = approval.reminder_due_at or approval_reminder_due_at(state)
    if current_time >= due_at:
        return f"Simulated review reminder is due ({due_at.isoformat()})."
    return f"Simulated review reminder is scheduled for {due_at.isoformat()}."
