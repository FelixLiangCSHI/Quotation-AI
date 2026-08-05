"""Authenticated, persistent approval workflow (Phase 6).

This module is the authority for approval behaviour. Every rule below is
enforced here, in the service/domain layer, so an alternative front end cannot
bypass it:

* allowed actions are derived deterministically from the Phase 5 decision;
* the acting user must be authenticated and hold the matching permission;
* a task must be open, current, and backed by fresh pricing and validation;
* an override approval requires a documented justification that explicitly
  acknowledges the margin is at or below the configured policy threshold;
* nothing is ever approved without an authorised human action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.approval_workflow import (
    ACTION_APPROVE,
    ACTION_APPROVE_WITH_OVERRIDE,
    ACTION_REJECT,
    ACTION_REQUEST_REVISION,
    InvalidApprovalTransitionError,
    approval_reminder_due_at,
    prepare_approval,
)
from app.auth.provider import AuthenticatedUser, PermissionDeniedError
from app.auth.roles import APPROVER_ROLES, Permission, Role, parse_role
from app.domain.dto import ApprovalTaskDTO
from app.domain.workflow_state_codec import dump_workflow_state
from app.margin_gate import (
    STATUS_BLOCKED,
    STATUS_PASS,
    STATUS_REVIEW_REQUIRED,
)
from app.quotation_models import (
    ApprovalRecord,
    ApprovalStatus,
    WorkflowStage,
    utc_now,
)
from app.services.quotation_service import LoadedQuotation, QuotationService
from app.services.unit_of_work import UnitOfWork
from app.workflow_state import append_audit_event

#: Deterministic action matrix. The UI must render exactly this set.
ALLOWED_ACTIONS_BY_DECISION: dict[str, tuple[str, ...]] = {
    STATUS_PASS: (ACTION_APPROVE, ACTION_REQUEST_REVISION),
    STATUS_REVIEW_REQUIRED: (
        ACTION_APPROVE_WITH_OVERRIDE,
        ACTION_REQUEST_REVISION,
        ACTION_REJECT,
    ),
    STATUS_BLOCKED: (ACTION_REQUEST_REVISION, ACTION_REJECT),
}

#: The permission each action requires.
PERMISSION_BY_ACTION: dict[str, Permission] = {
    ACTION_APPROVE: Permission.APPROVE_PASS,
    ACTION_APPROVE_WITH_OVERRIDE: Permission.APPROVE_WITH_OVERRIDE,
    ACTION_REQUEST_REVISION: Permission.REQUEST_REVISION,
    ACTION_REJECT: Permission.REJECT_QUOTATION,
}

#: Actions whose reason text is mandatory.
MANDATORY_REASON_ACTIONS = frozenset(
    {ACTION_APPROVE_WITH_OVERRIDE, ACTION_REQUEST_REVISION, ACTION_REJECT}
)

TASK_STATUS_PENDING = "pending_review"
TASK_STATUS_CANCELLED_STALE = "cancelled_stale"

#: The terminal states an approval task may reach.
COMPLETION_STATES = (
    ApprovalStatus.APPROVED.value,
    ApprovalStatus.APPROVED_WITH_OVERRIDE.value,
    ApprovalStatus.REVISION_REQUESTED.value,
    ApprovalStatus.REJECTED.value,
    TASK_STATUS_CANCELLED_STALE,
)

STATUS_BY_ACTION = {
    ACTION_APPROVE: ApprovalStatus.APPROVED,
    ACTION_APPROVE_WITH_OVERRIDE: ApprovalStatus.APPROVED_WITH_OVERRIDE,
    ACTION_REQUEST_REVISION: ApprovalStatus.REVISION_REQUESTED,
    ACTION_REJECT: ApprovalStatus.REJECTED,
}


class ApprovalServiceError(RuntimeError):
    """Base class for approval service failures."""


class StaleApprovalTaskError(ApprovalServiceError):
    """Raised when a task no longer matches the current quotation state."""


class ApprovalTaskCompletedError(ApprovalServiceError):
    """Raised when a completed task is acted on a second time."""


class MissingJustificationError(ApprovalServiceError):
    """Raised when a mandatory reason or override justification is absent."""


@dataclass(frozen=True)
class ApprovalTaskView:
    """Everything an authorised approver may see for one pending task."""

    task: ApprovalTaskDTO
    quotation_id: str
    quotation_version: int
    owner_user_id: int | None
    owner_username: str
    customer_name: str
    currency: str
    line_items: tuple[dict[str, Any], ...]
    total_revenue: str | None
    total_cost: str | None
    gross_margin_percent: str | None
    threshold_percent: str | None
    decision_status: str
    technical_validation_status: str
    data_quality_flags: tuple[str, ...]
    triggered_rule_ids: tuple[str, ...]
    ai_explanation: str
    ai_explanation_label: str
    allowed_actions: tuple[str, ...]
    policy_version_id: str
    is_stale: bool
    stale_reasons: tuple[str, ...]


def allowed_actions_for(decision_status: str) -> tuple[str, ...]:
    """Return the deterministic action set for a Phase 5 decision status."""

    return ALLOWED_ACTIONS_BY_DECISION.get(decision_status, ())


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class ApprovalService:
    """Authenticated approval use cases."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        quotation_service: QuotationService | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._quotations = quotation_service or QuotationService(session_factory)

    def _unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory)

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _require(user: AuthenticatedUser | None, permission: Permission) -> None:
        if user is None:
            raise PermissionDeniedError(
                "An authenticated user is required for this action."
            )
        user.require(permission)

    @staticmethod
    def _approver_role(user: AuthenticatedUser) -> Role:
        for role in user.roles:
            if role in APPROVER_ROLES:
                return role
        return user.primary_role

    # -- assignment ----------------------------------------------------

    def list_possible_approvers(self, user: AuthenticatedUser):
        """Return the stored internal users who may act as approvers.

        Submission must choose one of these; an approver is never typed in as
        free text.
        """

        self._require(user, Permission.SUBMIT_QUOTATION)
        with self._unit_of_work() as uow:
            everyone = uow.users.list_users(only_active=True)
        approver_values = {role.value for role in APPROVER_ROLES}
        return tuple(
            candidate
            for candidate in everyone
            if approver_values.intersection(candidate.roles)
        )

    # -- submission ----------------------------------------------------

    def submit_for_approval(
        self,
        loaded: LoadedQuotation,
        *,
        user: AuthenticatedUser,
        approver_user_id: int,
        request_id: str | None = None,
    ) -> ApprovalTaskDTO:
        """Create a persistent approval task for a stored internal approver."""

        self._require(user, Permission.SUBMIT_QUOTATION)
        state = loaded.state
        decision = state.combined_decision
        if decision is None or state.validation_stale:
            raise ApprovalServiceError(
                "Pricing and validation must be current before submission."
            )
        if not decision.policy_version_id:
            raise ApprovalServiceError(
                "The active commercial policy version is missing."
            )

        approval = prepare_approval(state)
        if approval.status != ApprovalStatus.PENDING_REVIEW:
            raise ApprovalServiceError(
                "The quotation is not ready for approval review."
            )

        reminder_due_at = approval.reminder_due_at or approval_reminder_due_at(
            state
        )
        action_request_id = request_id or uuid4().hex

        with self._unit_of_work() as uow:
            approver = uow.users.get(approver_user_id)
            if approver is None or not approver.is_active:
                raise ApprovalServiceError(
                    "The selected approver is not a known active internal user."
                )
            approver_roles = {
                parse_role(role) for role in approver.roles
            }
            if not approver_roles.intersection(APPROVER_ROLES):
                raise ApprovalServiceError(
                    "The selected user is not authorised to approve quotations."
                )
            assigned_role = sorted(
                approver_roles.intersection(APPROVER_ROLES),
                key=lambda role: role.value,
            )[0]

            if uow.approvals.get_open_task_id(loaded.quotation_id) is not None:
                raise ApprovalServiceError(
                    "An approval task is already open for this quotation."
                )

            # Persist the state change first so the task records the version
            # the approver will actually be reviewing.
            updated = uow.quotations.update(
                quotation_id=loaded.quotation_id,
                expected_version=loaded.version,
                state_document=dump_workflow_state(state),
                fields={"approval_status": approval.status.value},
            )
            task_id = uow.approvals.open_task(
                quotation_id=loaded.quotation_id,
                assigned_approver_name=approver.display_name
                or approver.username,
                assigned_approver_role=assigned_role.value,
                assigned_user_id=approver.id,
                due_at=reminder_due_at,
                reminder_due_at=reminder_due_at,
                quotation_version=updated.version,
                decision_status=decision.status,
                submitted_by_user_id=user.user_id,
                submitted_at=utc_now(),
                policy_version_id=decision.policy_version_id,
                pricing_run_id=decision.pricing_run_id,
                validation_run_id=decision.technical_validation_run_id,
            )
            uow.audit_events.append(
                quotation_id=loaded.quotation_id,
                event_type="approval_submitted",
                actor=user.username,
                actor_role=user.primary_role.value,
                actor_user_id=user.user_id,
                after_state=approval.status.value,
                quotation_version=updated.version,
                policy_version_id=decision.policy_version_id,
                request_id=action_request_id,
                details={
                    "decision_status": decision.status,
                    "pricing_run_id": decision.pricing_run_id,
                    "validation_run_id": decision.technical_validation_run_id,
                },
            )
            uow.audit_events.append(
                quotation_id=loaded.quotation_id,
                event_type="approver_assigned",
                actor=user.username,
                actor_role=user.primary_role.value,
                actor_user_id=user.user_id,
                quotation_version=updated.version,
                policy_version_id=decision.policy_version_id,
                request_id=action_request_id,
                details={
                    "approver_user_id": approver.id,
                    "approver_username": approver.username,
                    "approver_role": assigned_role.value,
                },
            )
            uow.commit()
            task = uow.approvals.get_task(task_id)

        assert task is not None
        return task

    # -- reads ---------------------------------------------------------

    def list_tasks(
        self,
        user: AuthenticatedUser,
        *,
        only_open: bool = True,
        assigned_to_me: bool = True,
    ) -> tuple[ApprovalTaskDTO, ...]:
        self._require(user, Permission.VIEW_APPROVAL_TASKS)
        with self._unit_of_work() as uow:
            return uow.approvals.list_tasks(
                assigned_user_id=user.user_id if assigned_to_me else None,
                statuses=(TASK_STATUS_PENDING,) if only_open else (),
            )

    def get_task_view(
        self, user: AuthenticatedUser, task_id: int
    ) -> ApprovalTaskView:
        """Assemble the approver's authenticated review page data."""

        self._require(user, Permission.VIEW_APPROVAL_TASKS)
        with self._unit_of_work() as uow:
            task = uow.approvals.get_task(task_id)
            if task is None:
                raise ApprovalServiceError(f"Unknown approval task: {task_id}")
            owner_username = ""
            record = uow.quotations.get_by_quotation_id(task.quotation_reference)
            if record is not None and record.owner_user_id is not None:
                owner = uow.users.get(record.owner_user_id)
                owner_username = "" if owner is None else owner.username

        loaded = self._quotations.load_quotation(task.quotation_reference)
        state = loaded.state
        decision = state.combined_decision
        pricing = state.quotation_pricing
        may_see_cost = user.has_permission(Permission.VIEW_COMMERCIAL_DETAIL)
        stale_reasons = self._staleness_reasons(task, loaded)

        explanation = state.pricing_explanation
        line_items = tuple(
            {
                "position": index,
                "product_id": item.product_id,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "category": item.category.value,
                "is_optional": item.is_optional,
            }
            for index, item in enumerate(state.draft.line_items)
        )

        return ApprovalTaskView(
            task=task,
            quotation_id=loaded.quotation_id,
            quotation_version=loaded.version,
            owner_user_id=None if record is None else record.owner_user_id,
            owner_username=owner_username,
            customer_name=state.draft.customer_name,
            currency=state.draft.currency,
            line_items=line_items,
            total_revenue=(
                None if pricing is None else pricing.total_revenue
            ),
            total_cost=(
                pricing.total_cost
                if pricing is not None and may_see_cost
                else None
            ),
            gross_margin_percent=(
                None if decision is None else decision.evaluated_margin_percent
            ),
            threshold_percent=(
                None if decision is None else decision.threshold_percent
            ),
            decision_status="" if decision is None else decision.status,
            technical_validation_status=(
                ""
                if state.technical_validation is None
                else state.technical_validation.status
            ),
            data_quality_flags=(
                () if pricing is None else tuple(pricing.missing_data_flags)
            ),
            triggered_rule_ids=(
                () if decision is None else tuple(decision.triggered_rule_ids)
            ),
            ai_explanation="" if explanation is None else explanation.explanation,
            ai_explanation_label=(
                ""
                if explanation is None
                else explanation.label
            ),
            allowed_actions=(
                ()
                if stale_reasons or not task.is_open or decision is None
                else allowed_actions_for(decision.status)
            ),
            policy_version_id=task.policy_version_id,
            is_stale=bool(stale_reasons),
            stale_reasons=stale_reasons,
        )

    # -- staleness -----------------------------------------------------

    @staticmethod
    def _staleness_reasons(
        task: ApprovalTaskDTO, loaded: LoadedQuotation
    ) -> tuple[str, ...]:
        state = loaded.state
        decision = state.combined_decision
        reasons: list[str] = []
        if task.quotation_version != loaded.version:
            reasons.append("quotation_version_changed")
        if state.validation_stale:
            reasons.append("validation_stale")
        if decision is None:
            reasons.append("logical_decision_missing")
            return tuple(reasons)
        if state.pricing_result is None and state.quotation_pricing is None:
            reasons.append("pricing_stale")
        if task.pricing_run_id and task.pricing_run_id != decision.pricing_run_id:
            reasons.append("pricing_stale")
        if (
            task.validation_run_id
            and task.validation_run_id != decision.technical_validation_run_id
        ):
            reasons.append("validation_stale")
        if not decision.policy_version_id:
            reasons.append("policy_version_missing")
        elif (
            task.policy_version_id
            and task.policy_version_id != decision.policy_version_id
        ):
            reasons.append("policy_version_changed")
        if task.decision_status and task.decision_status != decision.status:
            reasons.append("decision_status_changed")
        # Preserve order while removing duplicates.
        seen: list[str] = []
        for reason in reasons:
            if reason not in seen:
                seen.append(reason)
        return tuple(seen)

    # -- decision ------------------------------------------------------

    def act(
        self,
        *,
        user: AuthenticatedUser,
        task_id: int,
        action: str,
        reason: str = "",
        acknowledge_below_threshold: bool = False,
        final_unit_price: float | None = None,
        action_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> ApprovalTaskDTO:
        """Apply one approval action atomically.

        Every guard runs before anything is written, and the whole write is a
        single transaction, so a refused or failed action persists nothing.
        """

        normalized_action = (action or "").strip().casefold()
        normalized_reason = (reason or "").strip()
        request_id = action_id or uuid4().hex
        moment = timestamp or utc_now()

        if normalized_action not in PERMISSION_BY_ACTION:
            raise InvalidApprovalTransitionError(
                f"Unknown approval action: {action!r}"
            )
        self._require(user, PERMISSION_BY_ACTION[normalized_action])

        with self._unit_of_work() as uow:
            task_row = uow.approvals.get_task(task_id)
            if task_row is None:
                raise ApprovalServiceError(f"Unknown approval task: {task_id}")
            if task_row.status != TASK_STATUS_PENDING:
                raise ApprovalTaskCompletedError(
                    "This approval task has already been completed."
                )
            if (
                task_row.assigned_user_id is not None
                and task_row.assigned_user_id != user.user_id
                and not user.has_permission(Permission.CONFIGURE_SYSTEM)
            ):
                raise PermissionDeniedError(
                    "This approval task is assigned to another approver."
                )

            loaded = self._quotations.load_quotation(task_row.quotation_reference)
            state = loaded.state
            decision = state.combined_decision
            stale_reasons = self._staleness_reasons(task_row, loaded)
            if stale_reasons:
                raise StaleApprovalTaskError(
                    "The approval task is no longer current: "
                    + ", ".join(stale_reasons)
                )
            assert decision is not None

            allowed = allowed_actions_for(decision.status)
            if normalized_action not in allowed:
                raise InvalidApprovalTransitionError(
                    f"Action {normalized_action!r} is not allowed for decision "
                    f"{decision.status!r}."
                )

            if (
                normalized_action in MANDATORY_REASON_ACTIONS
                and not normalized_reason
            ):
                raise MissingJustificationError(
                    "A written reason is required for this action."
                )
            if normalized_action == ACTION_APPROVE_WITH_OVERRIDE:
                if not acknowledge_below_threshold:
                    raise MissingJustificationError(
                        "The override justification must acknowledge that the "
                        "quotation margin is equal to or below the configured "
                        "policy threshold of "
                        f"{decision.threshold_percent}%."
                    )

            recommended_price = (
                None
                if state.pricing_result is None
                else state.pricing_result.recommended_unit_price
            )
            final_price = (
                float(final_unit_price)
                if final_unit_price is not None
                else (
                    None if recommended_price is None else float(recommended_price)
                )
            )
            if normalized_action in {
                ACTION_APPROVE,
                ACTION_APPROVE_WITH_OVERRIDE,
            }:
                if recommended_price is None or final_price is None:
                    raise ApprovalServiceError(
                        "A recommended price is required before approval."
                    )
                if abs(final_price - float(recommended_price)) >= 0.005:
                    raise InvalidApprovalTransitionError(
                        "A changed price must be applied as a controlled "
                        "quotation edit, which reruns pricing, margin and the "
                        "logical judgement before a new approval."
                    )

            new_status = STATUS_BY_ACTION[normalized_action]
            before_status = state.approval.status.value
            state.approval = ApprovalRecord(
                status=new_status,
                actor=user.username,
                actor_role=self._approver_role(user).value,
                action=normalized_action,
                reason=normalized_reason,
                original_price=(
                    None if recommended_price is None else float(recommended_price)
                ),
                final_price=(
                    final_price
                    if normalized_action
                    in {ACTION_APPROVE, ACTION_APPROVE_WITH_OVERRIDE}
                    else None
                ),
                timestamp=moment,
                triggered_rule_ids=list(decision.triggered_rule_ids),
                override_justification=(
                    normalized_reason
                    if normalized_action == ACTION_APPROVE_WITH_OVERRIDE
                    else ""
                ),
                action_id=request_id,
                reminder_due_at=task_row.reminder_due_at,
            )
            state.current_stage = {
                ApprovalStatus.APPROVED: WorkflowStage.APPROVED,
                ApprovalStatus.APPROVED_WITH_OVERRIDE: WorkflowStage.APPROVED,
                ApprovalStatus.REJECTED: WorkflowStage.REJECTED,
                ApprovalStatus.REVISION_REQUESTED: WorkflowStage.REVIEW_REQUIRED,
            }[new_status]
            append_audit_event(
                state,
                f"approval_{normalized_action}",
                actor=user.username,
                before_state=before_status,
                after_state=new_status.value,
                reason=normalized_reason,
                triggered_rule_ids=list(decision.triggered_rule_ids),
                timestamp=moment,
            )

            action_row_id = uow.approvals.record_action(
                task_id=task_id,
                action_id=request_id,
                action=normalized_action,
                from_status=before_status,
                to_status=new_status.value,
                actor_name=user.username,
                actor_role=self._approver_role(user).value,
                actor_user_id=user.user_id,
                reason=normalized_reason,
                original_unit_price=recommended_price,
                final_unit_price=state.approval.final_price,
                triggered_rule_ids=tuple(decision.triggered_rule_ids),
                occurred_at=moment,
                quotation_version=loaded.version,
            )

            if normalized_action == ACTION_APPROVE_WITH_OVERRIDE:
                uow.approvals.record_override(
                    task_id=task_id,
                    approval_action_id=action_row_id,
                    original_decision=decision.status,
                    evaluated_margin_percent=str(
                        decision.evaluated_margin_percent or ""
                    ),
                    policy_threshold_percent=str(
                        decision.threshold_percent or ""
                    ),
                    policy_version_id=decision.policy_version_id,
                    approver_user_id=user.user_id,
                    approver_name=user.username,
                    approver_role=self._approver_role(user).value,
                    justification=normalized_reason,
                    final_approved_price=state.approval.final_price,
                    final_margin_percent=str(
                        decision.evaluated_margin_percent or ""
                    ),
                    triggered_rule_ids=tuple(decision.triggered_rule_ids),
                    occurred_at=moment,
                )
                uow.audit_events.append(
                    quotation_id=loaded.quotation_id,
                    event_type="override_justification_recorded",
                    actor=user.username,
                    actor_role=self._approver_role(user).value,
                    actor_user_id=user.user_id,
                    reason=normalized_reason,
                    quotation_version=loaded.version,
                    policy_version_id=decision.policy_version_id,
                    request_id=request_id,
                    triggered_rule_ids=tuple(decision.triggered_rule_ids),
                    details={
                        "original_decision": decision.status,
                        "evaluated_margin_percent": str(
                            decision.evaluated_margin_percent or ""
                        ),
                        "policy_threshold_percent": str(
                            decision.threshold_percent or ""
                        ),
                    },
                )

            uow.quotations.update(
                quotation_id=loaded.quotation_id,
                expected_version=loaded.version,
                state_document=dump_workflow_state(state),
                fields={
                    "status": state.current_stage.value,
                    "approval_status": new_status.value,
                },
            )
            uow.audit_events.append(
                quotation_id=loaded.quotation_id,
                event_type=f"approval_{normalized_action}",
                actor=user.username,
                actor_role=self._approver_role(user).value,
                actor_user_id=user.user_id,
                before_state=before_status,
                after_state=new_status.value,
                reason=normalized_reason,
                triggered_rule_ids=tuple(decision.triggered_rule_ids),
                quotation_version=loaded.version,
                policy_version_id=decision.policy_version_id,
                request_id=request_id,
                occurred_at=moment,
            )
            uow.commit()
            updated = uow.approvals.get_task(task_id)

        assert updated is not None
        return updated

    # -- material edits ------------------------------------------------

    def cancel_open_tasks_for_material_edit(
        self,
        quotation_id: str,
        *,
        user: AuthenticatedUser | None = None,
        reason: str = "Material quotation edit after submission.",
        quotation_version: int = 0,
    ) -> tuple[int, ...]:
        """Cancel any open task because the quotation materially changed."""

        actor = "system" if user is None else user.username
        actor_role = "" if user is None else user.primary_role.value
        with self._unit_of_work() as uow:
            cancelled = uow.approvals.cancel_open_tasks(
                quotation_id=quotation_id, reason=reason
            )
            for task_id in cancelled:
                uow.audit_events.append(
                    quotation_id=quotation_id,
                    event_type="approval_task_cancelled_stale",
                    actor=actor,
                    actor_role=actor_role,
                    actor_user_id=None if user is None else user.user_id,
                    after_state=TASK_STATUS_CANCELLED_STALE,
                    reason=reason,
                    quotation_version=quotation_version,
                    details={"approval_task_id": task_id},
                )
            uow.commit()
        return cancelled


def approval_status_is_complete(status: str) -> bool:
    return status in COMPLETION_STATES


class MaterialEditError(ApprovalServiceError):
    """Raised when a material edit cannot be applied."""


def apply_material_edit(
    quotation_id: str,
    *,
    user: AuthenticatedUser,
    edits: dict[str, Any],
    approval_service: "ApprovalService",
    quotation_service: QuotationService | None = None,
) -> LoadedQuotation:
    """Apply a controlled quotation edit after submission.

    A material edit increments the quotation version, cancels any open
    approval task as ``cancelled_stale``, drops the approval, invalidates the
    generated customer outputs, and forces pricing and validation to rerun
    before the quotation may be resubmitted.
    """

    from app.workflow_validation import apply_quotation_edits

    user.require(Permission.EDIT_OWN_DRAFT)
    service = quotation_service or QuotationService()
    loaded = service.load_quotation(quotation_id)
    changed = apply_quotation_edits(loaded.state, **edits)
    if not changed:
        return loaded

    saved = service.save_state(
        loaded,
        event_type="quotation_material_edit",
        actor=user.username,
        actor_user_id=user.user_id,
        changed_fields=changed,
    )
    approval_service.cancel_open_tasks_for_material_edit(
        quotation_id,
        user=user,
        reason="Material quotation edit after submission.",
        quotation_version=saved.version,
    )
    return service.load_quotation(quotation_id)
