"""Quotation persistence service.

This is the transaction boundary between the UI and the database. It reuses
the existing deterministic engines unchanged: the workflow state produced and
consumed here is the same :class:`QuotationWorkflowState` that the pricing
engine, rule engine, validation modules and approval state machine already
operate on.

Every material workflow event is written as an ``AuditEvent`` row in the same
transaction as the state change that produced it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from app.approval_workflow import (
    approval_reminder_due_at,
    prepare_approval,
    submit_approval_action,
)
from app.domain.dto import (
    LineItemDTO,
    LineItemType,
    QuotationDTO,
    QuotationSummaryDTO,
)
from app.domain.workflow_state_codec import (
    dump_workflow_state,
    load_workflow_state,
)
from app.quotation_models import (
    ApprovalStatus,
    LineItemCategory,
    QuotationWorkflowState,
    WorkflowStage,
    utc_now,
)
from app.repositories.interfaces import QuotationNotFoundError
from app.services.unit_of_work import UnitOfWork
from app.workflow_state import generate_quotation_id, initialize_workflow_state
from app.workflow_validation import invalidate_validation_outputs


#: Domain line-item categories mapped onto the persisted line item types.
LINE_ITEM_TYPE_BY_CATEGORY = {
    LineItemCategory.MAIN_PRODUCT: LineItemType.MAIN_PRODUCT,
    LineItemCategory.ACCESSORY: LineItemType.ACCESSORY,
    LineItemCategory.INSTALLATION: LineItemType.INSTALLATION,
    LineItemCategory.WARRANTY: LineItemType.WARRANTY,
    LineItemCategory.SERVICE: LineItemType.SERVICE,
    LineItemCategory.COMMERCIAL_ADDITION: LineItemType.COMMERCIAL_ADDITION,
}


def line_item_dtos(draft) -> tuple[LineItemDTO, ...]:
    """Project the draft's line items onto persistable DTOs."""

    return tuple(
        LineItemDTO(
            position=position,
            item_type=LINE_ITEM_TYPE_BY_CATEGORY[item.category],
            product_id=item.product_id,
            customer_description=item.description,
            internal_description=item.notes,
            quantity=item.quantity,
            currency=draft.currency,
            proposed_unit_price=(
                None if item.unit_price is None else Decimal(str(item.unit_price))
            ),
            is_optional=item.is_optional,
        )
        for position, item in enumerate(draft.line_items)
    )


class QuotationServiceError(RuntimeError):
    """Raised when a quotation service operation cannot be completed."""


@dataclass(frozen=True)
class LoadedQuotation:
    """A persisted quotation together with its restored workflow state.

    ``record`` is a typed DTO safe to hand to the UI. ``state`` is the
    deterministic engine's working object.
    """

    record: QuotationDTO
    state: QuotationWorkflowState

    @property
    def quotation_id(self) -> str:
        return self.record.quotation_id

    @property
    def version(self) -> int:
        return self.record.version


class QuotationService:
    """Use cases for creating, loading and mutating persisted quotations."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._session_factory = session_factory

    def _unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory)

    # -- creation ------------------------------------------------------

    def create_quotation(
        self,
        *,
        quotation_id: str | None = None,
        owner_user_id: int | None = None,
        actor: str = "system",
        state: QuotationWorkflowState | None = None,
    ) -> LoadedQuotation:
        """Create and persist a new quotation.

        When ``state`` is supplied (for example by the synthetic demo mode) it
        is persisted as-is, so demo scenarios remain usable without change.
        """

        working_state = state or initialize_workflow_state(
            quotation_id=quotation_id or generate_quotation_id()
        )
        if quotation_id and working_state.draft.quotation_id != quotation_id:
            working_state.draft.quotation_id = quotation_id

        draft = working_state.draft
        with self._unit_of_work() as uow:
            record = uow.quotations.create(
                quotation_id=draft.quotation_id,
                state_document=dump_workflow_state(working_state),
                owner_user_id=owner_user_id,
                customer_name=draft.customer_name,
                customer_type=draft.customer_type,
                region=draft.region,
                currency=draft.currency,
                incoterm=draft.incoterm,
                delivery_location=draft.delivery_location,
                status=working_state.current_stage.value,
                approval_status=working_state.approval.status.value,
            )
            uow.audit_events.append(
                quotation_id=draft.quotation_id,
                event_type="quotation_created",
                actor=actor,
                actor_user_id=owner_user_id,
                after_state=working_state.approval.status.value,
                details={"source": "service"},
            )
            uow.commit()
            record = uow.quotations.get_by_quotation_id(draft.quotation_id)

        assert record is not None
        return LoadedQuotation(record=record, state=working_state)

    # -- reads ---------------------------------------------------------

    def load_quotation(self, quotation_id: str) -> LoadedQuotation:
        """Reload a quotation and rebuild its deterministic workflow state."""

        with self._unit_of_work() as uow:
            record = uow.quotations.get_by_quotation_id(quotation_id)
        if record is None:
            raise QuotationNotFoundError(f"Unknown quotation: {quotation_id}")
        return LoadedQuotation(
            record=record,
            state=load_workflow_state(record.state_document),
        )

    def list_quotations(
        self,
        *,
        owner_user_id: int | None = None,
        include_closed: bool = True,
    ) -> tuple[QuotationSummaryDTO, ...]:
        with self._unit_of_work() as uow:
            return uow.quotations.list_summaries(
                owner_user_id=owner_user_id,
                include_closed=include_closed,
            )

    def get_audit_trail(self, quotation_id: str):
        with self._unit_of_work() as uow:
            return uow.audit_events.list_for_quotation(quotation_id)

    # -- mutation ------------------------------------------------------

    def save_state(
        self,
        loaded: LoadedQuotation,
        *,
        event_type: str = "quotation_updated",
        actor: str = "system",
        actor_user_id: int | None = None,
        changed_fields: tuple[str, ...] = (),
        reason: str = "",
        details: dict[str, Any] | None = None,
        actor_role: str = "",
    ) -> LoadedQuotation:
        """Persist a mutated workflow state, guarded by its version."""

        state = loaded.state
        draft = state.draft
        before_status = loaded.record.approval_status

        with self._unit_of_work() as uow:
            uow.quotations.update(
                quotation_id=loaded.quotation_id,
                expected_version=loaded.version,
                state_document=dump_workflow_state(state),
                fields={
                    "customer_name": draft.customer_name,
                    "customer_type": draft.customer_type,
                    "region": draft.region,
                    "currency": draft.currency,
                    "incoterm": draft.incoterm,
                    "delivery_location": draft.delivery_location,
                    "status": state.current_stage.value,
                    "approval_status": state.approval.status.value,
                },
            )
            uow.audit_events.append(
                quotation_id=loaded.quotation_id,
                event_type=event_type,
                actor=actor,
                actor_role=actor_role,
                actor_user_id=actor_user_id,
                before_state=before_status,
                after_state=state.approval.status.value,
                changed_fields=changed_fields,
                reason=reason,
                details=details or {},
                policy_version_id=(
                    state.combined_decision.policy_version_id
                    if state.combined_decision is not None
                    else ""
                ),
            )
            uow.commit()
            record = uow.quotations.get_by_quotation_id(loaded.quotation_id)

        assert record is not None
        return LoadedQuotation(record=record, state=state)

    def mutate(
        self,
        quotation_id: str,
        mutation: Callable[[QuotationWorkflowState], Any],
        *,
        event_type: str = "quotation_updated",
        actor: str = "system",
        actor_user_id: int | None = None,
        changed_fields: tuple[str, ...] = (),
    ) -> LoadedQuotation:
        """Load, apply a mutation, and persist atomically.

        If ``mutation`` raises, nothing is written: no state change and no
        audit event.
        """

        loaded = self.load_quotation(quotation_id)
        mutation(loaded.state)
        return self.save_state(
            loaded,
            event_type=event_type,
            actor=actor,
            actor_user_id=actor_user_id,
            changed_fields=changed_fields,
        )

    def replace_line_items(
        self,
        loaded: LoadedQuotation,
        line_items: tuple[LineItemDTO, ...],
        *,
        actor: str = "system",
        actor_user_id: int | None = None,
    ) -> LoadedQuotation:
        """Replace the quotation's line items in a single transaction."""

        with self._unit_of_work() as uow:
            uow.quotations.replace_line_items(
                quotation_id=loaded.quotation_id,
                expected_version=loaded.version,
                line_items=line_items,
            )
            uow.audit_events.append(
                quotation_id=loaded.quotation_id,
                event_type="line_items_updated",
                actor=actor,
                actor_user_id=actor_user_id,
                changed_fields=("line_items",),
                details={"line_item_count": len(line_items)},
            )
            uow.commit()
            record = uow.quotations.get_by_quotation_id(loaded.quotation_id)

        assert record is not None
        return LoadedQuotation(record=record, state=loaded.state)

    def save_draft(
        self,
        state: QuotationWorkflowState,
        *,
        actor: str = "user",
        owner_user_id: int | None = None,
    ) -> LoadedQuotation:
        """Save a draft, creating it on first save and updating afterwards.

        This is the save half of draft save and resume. ``resume_draft``
        reloads the same quotation and rebuilds the deterministic state.
        """

        quotation_id = state.draft.quotation_id
        try:
            existing = self.load_quotation(quotation_id)
        except QuotationNotFoundError:
            created = self.create_quotation(
                quotation_id=quotation_id,
                owner_user_id=owner_user_id,
                actor=actor,
                state=state,
            )
            if state.draft.line_items:
                created = self.replace_line_items(
                    created,
                    line_item_dtos(state.draft),
                    actor=actor,
                    actor_user_id=owner_user_id,
                )
            return created
        existing = LoadedQuotation(record=existing.record, state=state)
        saved = self.save_state(
            existing,
            event_type="draft_saved",
            actor=actor,
            actor_user_id=owner_user_id,
        )
        return self.replace_line_items(
            saved,
            line_item_dtos(state.draft),
            actor=actor,
            actor_user_id=owner_user_id,
        )

    def resume_draft(self, quotation_id: str) -> LoadedQuotation:
        """Reopen a saved draft with its deterministic state restored."""

        return self.load_quotation(quotation_id)

    def duplicate_quotation(
        self,
        quotation_id: str,
        *,
        new_quotation_id: str | None = None,
        actor: str = "user",
        owner_user_id: int | None = None,
    ) -> LoadedQuotation:
        """Copy a quotation's requirements and line items into a new draft.

        Only requirement and line-item state is carried over. Pricing,
        validation, approval and generated documents are deliberately dropped:
        a copy must be re-priced and re-approved on its own merits.
        """

        return self._copy_quotation(
            quotation_id,
            new_quotation_id=new_quotation_id,
            actor=actor,
            owner_user_id=owner_user_id,
            event_type="quotation_duplicated",
            keep_audit_trail=False,
        )

    def clone_as_new_version(
        self,
        quotation_id: str,
        *,
        new_quotation_id: str | None = None,
        actor: str = "user",
        owner_user_id: int | None = None,
    ) -> LoadedQuotation:
        """Create a successor version of a quotation.

        Like a duplicate, but the source quotation is recorded so the version
        lineage stays auditable, and the source audit trail is carried over.
        """

        return self._copy_quotation(
            quotation_id,
            new_quotation_id=new_quotation_id,
            actor=actor,
            owner_user_id=owner_user_id,
            event_type="quotation_cloned_as_new_version",
            keep_audit_trail=True,
        )

    def _copy_quotation(
        self,
        quotation_id: str,
        *,
        new_quotation_id: str | None,
        actor: str,
        owner_user_id: int | None,
        event_type: str,
        keep_audit_trail: bool,
    ) -> LoadedQuotation:
        source = self.load_quotation(quotation_id)
        target_id = new_quotation_id or generate_quotation_id()

        copied = initialize_workflow_state(quotation_id=target_id)
        source_draft = source.state.draft
        copied_draft = deepcopy(source_draft)
        copied_draft.quotation_id = target_id
        copied_draft.created_at = copied.draft.created_at
        copied_draft.updated_at = copied.draft.updated_at
        copied_draft.status = (
            WorkflowStage.READY_FOR_ANALYSIS
            if not copied_draft.missing_fields and copied_draft.selected_product_ids
            else WorkflowStage.COLLECTING_REQUIREMENTS
        )
        copied_draft.proposed_unit_price = None
        copied.draft = copied_draft
        copied.current_stage = copied_draft.status
        if keep_audit_trail:
            copied.audit_events = [
                *deepcopy(source.state.audit_events),
                *copied.audit_events,
            ]

        # Copies never inherit derived commercial state.
        invalidate_validation_outputs(copied, clear_pricing=True)

        created = self.create_quotation(
            quotation_id=target_id,
            owner_user_id=owner_user_id if owner_user_id is not None
            else source.record.owner_user_id,
            actor=actor,
            state=copied,
        )
        return self.save_state(
            created,
            event_type=event_type,
            actor=actor,
            actor_user_id=owner_user_id,
            details={"source_quotation_id": quotation_id},
        )

    # -- lifecycle -----------------------------------------------------

    def close_quotation(
        self,
        loaded: LoadedQuotation,
        *,
        actor: str = "system",
        actor_user_id: int | None = None,
    ) -> LoadedQuotation:
        return self._set_closed(
            loaded,
            True,
            "quotation_closed",
            actor=actor,
            actor_user_id=actor_user_id,
        )

    def reopen_quotation(
        self,
        loaded: LoadedQuotation,
        *,
        actor: str = "system",
        actor_user_id: int | None = None,
    ) -> LoadedQuotation:
        return self._set_closed(
            loaded,
            False,
            "quotation_reopened",
            actor=actor,
            actor_user_id=actor_user_id,
        )

    def _set_closed(
        self,
        loaded: LoadedQuotation,
        is_closed: bool,
        event_type: str,
        *,
        actor: str,
        actor_user_id: int | None,
    ) -> LoadedQuotation:
        with self._unit_of_work() as uow:
            uow.quotations.set_closed(
                quotation_id=loaded.quotation_id,
                expected_version=loaded.version,
                is_closed=is_closed,
            )
            uow.audit_events.append(
                quotation_id=loaded.quotation_id,
                event_type=event_type,
                actor=actor,
                actor_user_id=actor_user_id,
                changed_fields=("is_closed",),
            )
            uow.commit()
            record = uow.quotations.get_by_quotation_id(loaded.quotation_id)

        assert record is not None
        return LoadedQuotation(record=record, state=loaded.state)

    def record_event(
        self,
        quotation_id: str,
        event_type: str,
        *,
        actor: str = "system",
        actor_role: str = "",
        actor_user_id: int | None = None,
        before_state: str = "",
        after_state: str = "",
        changed_fields: tuple[str, ...] = (),
        reason: str = "",
        triggered_rule_ids: tuple[str, ...] = (),
        details: dict[str, Any] | None = None,
        policy_version_id: str = "",
        quotation_version: int = 0,
        request_id: str = "",
    ):
        """Append a standalone audit event for a material workflow step.

        Used for events that do not themselves change persisted state, such as
        a pricing run, a technical validation, a margin calculation, the
        logical judgement, or customer-output generation.
        """

        with self._unit_of_work() as uow:
            event = uow.audit_events.append(
                quotation_id=quotation_id,
                event_type=event_type,
                actor=actor,
                actor_role=actor_role,
                actor_user_id=actor_user_id,
                before_state=before_state,
                after_state=after_state,
                changed_fields=changed_fields,
                reason=reason,
                triggered_rule_ids=triggered_rule_ids,
                details=details or {},
                policy_version_id=policy_version_id,
                quotation_version=quotation_version,
                request_id=request_id,
            )
            uow.commit()
        return event

    # -- approval ------------------------------------------------------

    def submit_for_approval(
        self,
        loaded: LoadedQuotation,
        *,
        approver_name: str = "",
        approver_role: str = "",
        approver_user_id: int | None = None,
        actor: str = "system",
    ) -> LoadedQuotation:
        """Open an approval task once the deterministic gate allows it.

        The gate is :func:`app.approval_workflow.prepare_approval`; this
        service adds no new approval permissions.
        """

        approval = prepare_approval(loaded.state)
        if approval.status != ApprovalStatus.PENDING_REVIEW:
            raise QuotationServiceError(
                "The quotation is not ready for approval review."
            )

        reminder_due_at = approval.reminder_due_at or approval_reminder_due_at(
            loaded.state
        )
        with self._unit_of_work() as uow:
            uow.quotations.update(
                quotation_id=loaded.quotation_id,
                expected_version=loaded.version,
                state_document=dump_workflow_state(loaded.state),
                fields={"approval_status": approval.status.value},
            )
            uow.approvals.open_task(
                quotation_id=loaded.quotation_id,
                assigned_approver_name=approver_name,
                assigned_approver_role=approver_role,
                assigned_user_id=approver_user_id,
                due_at=reminder_due_at,
                reminder_due_at=reminder_due_at,
            )
            uow.audit_events.append(
                quotation_id=loaded.quotation_id,
                event_type="approval_requested",
                actor=actor,
                after_state=approval.status.value,
                details={"approver": approver_name, "role": approver_role},
            )
            uow.commit()
            record = uow.quotations.get_by_quotation_id(loaded.quotation_id)

        assert record is not None
        return LoadedQuotation(record=record, state=loaded.state)

    def decide_approval(
        self,
        loaded: LoadedQuotation,
        *,
        action: str,
        actor_role: str,
        actor_name: str = "",
        actor_user_id: int | None = None,
        reason: str = "",
        final_unit_price: float | None = None,
        action_id: str | None = None,
        timestamp: datetime | None = None,
    ) -> LoadedQuotation:
        """Apply an approval action and persist the outcome.

        The decision itself is delegated unchanged to
        :func:`app.approval_workflow.submit_approval_action`, so every existing
        restriction still applies: allowed actions per combined decision,
        approver role membership, mandatory reasons, the price-change guard,
        and idempotency. If it raises, nothing is persisted.
        """

        state = loaded.state
        before_status = state.approval.status.value

        # Deterministic gate first. A rejection here writes nothing.
        approval = submit_approval_action(
            state,
            action=action,
            actor_role=actor_role,
            actor_name=actor_name,
            reason=reason,
            final_unit_price=final_unit_price,
            action_id=action_id,
            timestamp=timestamp,
        )

        with self._unit_of_work() as uow:
            task_id = uow.approvals.get_open_task_id(loaded.quotation_id)
            if task_id is None:
                task_id = uow.approvals.open_task(
                    quotation_id=loaded.quotation_id,
                    assigned_approver_name=actor_name,
                    assigned_approver_role=actor_role,
                    assigned_user_id=actor_user_id,
                    reminder_due_at=approval.reminder_due_at,
                )
            uow.approvals.record_action(
                task_id=task_id,
                action_id=approval.action_id,
                action=approval.action,
                from_status=before_status,
                to_status=approval.status.value,
                actor_name=approval.actor,
                actor_role=approval.actor_role,
                actor_user_id=actor_user_id,
                reason=approval.reason,
                original_unit_price=approval.original_price,
                final_unit_price=approval.final_price,
                triggered_rule_ids=tuple(approval.triggered_rule_ids),
                occurred_at=approval.timestamp,
            )
            uow.quotations.update(
                quotation_id=loaded.quotation_id,
                expected_version=loaded.version,
                state_document=dump_workflow_state(state),
                fields={
                    "status": state.current_stage.value,
                    "approval_status": approval.status.value,
                },
            )
            uow.audit_events.append(
                quotation_id=loaded.quotation_id,
                event_type=f"approval_{approval.action}",
                actor=approval.actor,
                actor_user_id=actor_user_id,
                before_state=before_status,
                after_state=approval.status.value,
                reason=approval.reason,
                triggered_rule_ids=tuple(approval.triggered_rule_ids),
                occurred_at=approval.timestamp or utc_now(),
            )
            uow.commit()
            record = uow.quotations.get_by_quotation_id(loaded.quotation_id)

        assert record is not None
        return LoadedQuotation(record=record, state=state)
