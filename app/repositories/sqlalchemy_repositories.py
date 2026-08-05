"""SQLAlchemy implementations of the repository interfaces.

Repositories never commit. The transaction boundary belongs to the service
layer's unit of work, so a service failure rolls back every write it made.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import models
from app.domain.dto import (
    ApprovalTaskDTO,
    AuditEventDTO,
    EmailRecordDTO,
    LineItemDTO,
    LineItemType,
    QuotationDTO,
    QuotationSummaryDTO,
    UserDTO,
)
from app.quotation_models import utc_now
from app.repositories.interfaces import (
    DuplicateApprovalActionError,
    QuotationNotFoundError,
    QuotationVersionConflictError,
    RepositoryError,
)

#: Quotation columns a service may update through ``update(fields=...)``.
#: Deliberately narrow: ``version`` and ``id`` are managed by the repository.
UPDATABLE_QUOTATION_FIELDS = frozenset(
    {
        "customer_name",
        "customer_type",
        "region",
        "currency",
        "incoterm",
        "delivery_location",
        "status",
        "approval_status",
        "owner_user_id",
        "pricing_data_version_id",
    }
)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class SqlAlchemyUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        username: str,
        display_name: str = "",
        email: str = "",
        roles: tuple[str, ...] = (),
        password_hash: str = "",
        auth_provider: str = "local",
        external_subject: str = "",
    ) -> UserDTO:
        record = models.User(
            username=username,
            display_name=display_name or username,
            email=email,
            roles=list(roles),
            password_hash=password_hash,
            auth_provider=auth_provider,
            external_subject=external_subject,
        )
        self._session.add(record)
        self._session.flush()
        return _user_dto(record)

    def get_by_username(self, username: str) -> UserDTO | None:
        record = self._session.scalars(
            select(models.User).where(models.User.username == username)
        ).one_or_none()
        return _user_dto(record) if record is not None else None

    def get(self, user_id: int) -> UserDTO | None:
        record = self._session.get(models.User, user_id)
        return _user_dto(record) if record is not None else None

    def get_credential(self, username: str) -> models.User | None:
        """Return the ORM row. Used only by the authentication provider."""

        return self._session.scalars(
            select(models.User).where(models.User.username == username)
        ).one_or_none()

    def list_users(self, *, only_active: bool = True) -> tuple[UserDTO, ...]:
        statement = select(models.User).order_by(models.User.id)
        if only_active:
            statement = statement.where(models.User.is_active.is_(True))
        return tuple(
            _user_dto(record) for record in self._session.scalars(statement)
        )

    def list_by_role(self, role: str) -> tuple[UserDTO, ...]:
        return tuple(
            user
            for user in self.list_users(only_active=True)
            if role in user.roles
        )

    def set_password_hash(self, *, user_id: int, password_hash: str) -> None:
        record = self._session.get(models.User, user_id)
        if record is None:
            raise RepositoryError(f"Unknown user: {user_id}")
        record.password_hash = password_hash
        self._session.flush()

    def set_roles(self, *, user_id: int, roles: tuple[str, ...]) -> UserDTO:
        record = self._session.get(models.User, user_id)
        if record is None:
            raise RepositoryError(f"Unknown user: {user_id}")
        record.roles = list(roles)
        self._session.flush()
        return _user_dto(record)

    def record_login(self, *, user_id: int, moment: datetime) -> None:
        record = self._session.get(models.User, user_id)
        if record is not None:
            record.last_login_at = moment
            self._session.flush()

    # -- authenticated sessions ----------------------------------------

    def create_session(
        self,
        *,
        user_id: int,
        token: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> int:
        record = models.UserSession(
            token=token,
            user_id=user_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        self._session.add(record)
        self._session.flush()
        return record.id

    def get_session(self, token: str) -> models.UserSession | None:
        return self._session.scalars(
            select(models.UserSession).where(models.UserSession.token == token)
        ).one_or_none()

    def revoke_session(self, token: str, *, moment: datetime) -> None:
        record = self.get_session(token)
        if record is not None and record.revoked_at is None:
            record.revoked_at = moment
            self._session.flush()


class SqlAlchemyQuotationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # -- reads ---------------------------------------------------------

    def _record(self, quotation_id: str) -> models.Quotation:
        record = self._session.scalars(
            select(models.Quotation)
            .where(models.Quotation.quotation_id == quotation_id)
            .options(
                selectinload(models.Quotation.line_items),
                selectinload(models.Quotation.audit_events),
            )
        ).one_or_none()
        if record is None:
            raise QuotationNotFoundError(f"Unknown quotation: {quotation_id}")
        return record

    def get_by_quotation_id(self, quotation_id: str) -> QuotationDTO | None:
        try:
            return _quotation_dto(self._record(quotation_id))
        except QuotationNotFoundError:
            return None

    def get(self, primary_key: int) -> QuotationDTO | None:
        record = self._session.get(models.Quotation, primary_key)
        return _quotation_dto(record) if record is not None else None

    def list_summaries(
        self,
        *,
        owner_user_id: int | None = None,
        include_closed: bool = True,
    ) -> tuple[QuotationSummaryDTO, ...]:
        statement = select(models.Quotation)
        if owner_user_id is not None:
            statement = statement.where(
                models.Quotation.owner_user_id == owner_user_id
            )
        if not include_closed:
            statement = statement.where(models.Quotation.is_closed.is_(False))
        statement = statement.order_by(models.Quotation.id)
        return tuple(
            _summary_dto(record) for record in self._session.scalars(statement)
        )

    # -- writes --------------------------------------------------------

    def create(
        self,
        *,
        quotation_id: str,
        state_document: dict[str, Any],
        owner_user_id: int | None = None,
        customer_name: str = "",
        customer_type: str = "",
        region: str = "",
        currency: str = "USD",
        incoterm: str = "",
        delivery_location: str = "",
        status: str = "draft",
        approval_status: str = "not_ready",
        pricing_data_version_id: int | None = None,
    ) -> QuotationDTO:
        record = models.Quotation(
            quotation_id=quotation_id,
            owner_user_id=owner_user_id,
            customer_name=customer_name,
            customer_type=customer_type,
            region=region,
            currency=currency,
            incoterm=incoterm,
            delivery_location=delivery_location,
            status=status,
            approval_status=approval_status,
            pricing_data_version_id=pricing_data_version_id,
            state_document=dict(state_document),
            version=1,
        )
        self._session.add(record)
        self._session.flush()
        return _quotation_dto(record)

    def _guard_version(
        self, record: models.Quotation, expected_version: int
    ) -> None:
        if record.version != expected_version:
            raise QuotationVersionConflictError(
                record.quotation_id, expected_version, record.version
            )

    def update(
        self,
        *,
        quotation_id: str,
        expected_version: int,
        state_document: dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> QuotationDTO:
        record = self._record(quotation_id)
        self._guard_version(record, expected_version)

        for name, value in (fields or {}).items():
            if name not in UPDATABLE_QUOTATION_FIELDS:
                raise ValueError(f"Field {name!r} is not updatable.")
            setattr(record, name, value)

        if state_document is not None:
            # Reassign rather than mutate so the JSON column is marked dirty.
            record.state_document = dict(state_document)

        record.version = expected_version + 1
        record.updated_at = utc_now()
        self._session.flush()
        return _quotation_dto(record)

    def replace_line_items(
        self,
        *,
        quotation_id: str,
        expected_version: int,
        line_items: tuple[LineItemDTO, ...],
    ) -> QuotationDTO:
        record = self._record(quotation_id)
        self._guard_version(record, expected_version)

        positions = [item.position for item in line_items]
        if len(set(positions)) != len(positions):
            raise ValueError("Line item positions must be unique.")

        record.line_items.clear()
        # Flush the removals before inserting so the unique constraint on
        # (quotation_id, position) is not violated by a reused position.
        self._session.flush()

        for item in line_items:
            record.line_items.append(
                models.QuotationLineItem(
                    position=item.position,
                    item_type=LineItemType(item.item_type).value,
                    product_id=item.product_id,
                    internal_description=item.internal_description,
                    customer_description=item.customer_description,
                    quantity=item.quantity,
                    currency=item.currency,
                    proposed_unit_price=_to_decimal(item.proposed_unit_price),
                    approved_unit_price=_to_decimal(item.approved_unit_price),
                    list_unit_price=_to_decimal(item.list_unit_price),
                    discount_percent=_to_decimal(item.discount_percent),
                    is_optional=item.is_optional,
                )
            )

        record.version = expected_version + 1
        record.updated_at = utc_now()
        self._session.flush()
        return _quotation_dto(record)

    def set_closed(
        self,
        *,
        quotation_id: str,
        expected_version: int,
        is_closed: bool,
    ) -> QuotationDTO:
        record = self._record(quotation_id)
        self._guard_version(record, expected_version)
        record.is_closed = is_closed
        record.version = expected_version + 1
        record.updated_at = utc_now()
        self._session.flush()
        return _quotation_dto(record)


class SqlAlchemyAuditEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        *,
        quotation_id: str,
        event_type: str,
        actor: str = "system",
        actor_user_id: int | None = None,
        before_state: str = "",
        after_state: str = "",
        changed_fields: tuple[str, ...] = (),
        reason: str = "",
        triggered_rule_ids: tuple[str, ...] = (),
        details: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
        actor_role: str = "",
        quotation_version: int = 0,
        policy_version_id: str = "",
        request_id: str = "",
    ) -> AuditEventDTO:
        normalized_type = event_type.strip()
        normalized_actor = actor.strip()
        if not normalized_type:
            raise ValueError("event_type cannot be blank")
        if not normalized_actor:
            raise ValueError("actor cannot be blank")

        parent = self._session.scalars(
            select(models.Quotation).where(
                models.Quotation.quotation_id == quotation_id
            )
        ).one_or_none()

        record = models.AuditEventRecord(
            quotation_id=parent.id if parent is not None else None,
            quotation_reference=quotation_id,
            event_type=normalized_type,
            actor=normalized_actor,
            actor_role=actor_role,
            actor_user_id=actor_user_id,
            quotation_version=quotation_version or (
                parent.version if parent is not None else 0
            ),
            policy_version_id=policy_version_id,
            request_id=request_id,
            before_state=before_state,
            after_state=after_state,
            changed_fields=list(changed_fields),
            reason=reason,
            triggered_rule_ids=list(triggered_rule_ids),
            details=dict(details or {}),
            occurred_at=occurred_at or utc_now(),
        )
        self._session.add(record)
        self._session.flush()
        return _audit_dto(record)

    def list_recent(self, *, limit: int = 200) -> tuple[AuditEventDTO, ...]:
        statement = (
            select(models.AuditEventRecord)
            .order_by(models.AuditEventRecord.id.desc())
            .limit(limit)
        )
        return tuple(
            _audit_dto(record)
            for record in reversed(list(self._session.scalars(statement)))
        )

    def list_for_quotation(self, quotation_id: str) -> tuple[AuditEventDTO, ...]:
        statement = (
            select(models.AuditEventRecord)
            .where(models.AuditEventRecord.quotation_reference == quotation_id)
            .order_by(models.AuditEventRecord.id)
        )
        return tuple(
            _audit_dto(record) for record in self._session.scalars(statement)
        )


class SqlAlchemyApprovalRepository:
    #: Task states that still await an approver decision.
    OPEN_STATUSES = ("pending_review",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def open_task(
        self,
        *,
        quotation_id: str,
        assigned_approver_name: str = "",
        assigned_approver_role: str = "",
        assigned_user_id: int | None = None,
        due_at: datetime | None = None,
        reminder_due_at: datetime | None = None,
        task_reference: str = "",
        quotation_version: int = 0,
        decision_status: str = "",
        submitted_by_user_id: int | None = None,
        submitted_at: datetime | None = None,
        policy_version_id: str = "",
        pricing_run_id: str = "",
        validation_run_id: str = "",
    ) -> int:
        parent = self._session.scalars(
            select(models.Quotation).where(
                models.Quotation.quotation_id == quotation_id
            )
        ).one_or_none()
        if parent is None:
            raise QuotationNotFoundError(f"Unknown quotation: {quotation_id}")

        existing = self.get_open_task_id(quotation_id)
        if existing is not None:
            return existing

        record = models.ApprovalTask(
            quotation_id=parent.id,
            quotation_reference=quotation_id,
            task_reference=task_reference or f"TASK-{uuid4().hex[:12].upper()}",
            quotation_version=quotation_version or parent.version,
            decision_status=decision_status,
            assigned_user_id=assigned_user_id,
            assigned_approver_name=assigned_approver_name,
            assigned_approver_role=assigned_approver_role,
            submitted_by_user_id=submitted_by_user_id,
            submitted_at=submitted_at or utc_now(),
            status="pending_review",
            due_at=due_at,
            reminder_due_at=reminder_due_at,
            policy_version_id=policy_version_id,
            pricing_run_id=pricing_run_id,
            validation_run_id=validation_run_id,
        )
        self._session.add(record)
        self._session.flush()
        return record.id

    def get_open_task_id(self, quotation_id: str) -> int | None:
        record = self._session.scalars(
            select(models.ApprovalTask)
            .join(models.Quotation)
            .where(
                models.Quotation.quotation_id == quotation_id,
                models.ApprovalTask.status.in_(self.OPEN_STATUSES),
            )
            .order_by(models.ApprovalTask.id.desc())
        ).first()
        return record.id if record is not None else None

    def get_task(self, task_id: int) -> ApprovalTaskDTO | None:
        record = self._session.get(models.ApprovalTask, task_id)
        return _approval_task_dto(record) if record is not None else None

    def get_open_task(self, quotation_id: str) -> ApprovalTaskDTO | None:
        task_id = self.get_open_task_id(quotation_id)
        return None if task_id is None else self.get_task(task_id)

    def lock_open_task(self, quotation_id: str) -> models.ApprovalTask | None:
        """Read the open task inside the current transaction for update.

        The row lock makes two concurrent approval attempts serialise, so the
        second observes the completed status instead of racing it.
        """

        statement = (
            select(models.ApprovalTask)
            .join(models.Quotation)
            .where(
                models.Quotation.quotation_id == quotation_id,
                models.ApprovalTask.status.in_(self.OPEN_STATUSES),
            )
            .order_by(models.ApprovalTask.id.desc())
        )
        if self._session.bind is not None and self._session.bind.dialect.name != "sqlite":
            statement = statement.with_for_update()
        return self._session.scalars(statement).first()

    def list_tasks(
        self,
        *,
        assigned_user_id: int | None = None,
        statuses: tuple[str, ...] = (),
        quotation_id: str | None = None,
    ) -> tuple[ApprovalTaskDTO, ...]:
        statement = select(models.ApprovalTask)
        if assigned_user_id is not None:
            statement = statement.where(
                models.ApprovalTask.assigned_user_id == assigned_user_id
            )
        if statuses:
            statement = statement.where(
                models.ApprovalTask.status.in_(statuses)
            )
        if quotation_id is not None:
            statement = statement.where(
                models.ApprovalTask.quotation_reference == quotation_id
            )
        statement = statement.order_by(models.ApprovalTask.id)
        return tuple(
            _approval_task_dto(record)
            for record in self._session.scalars(statement)
        )

    def cancel_open_tasks(
        self,
        *,
        quotation_id: str,
        reason: str = "",
        moment: datetime | None = None,
    ) -> tuple[int, ...]:
        """Mark every open task on a quotation as ``cancelled_stale``."""

        timestamp = moment or utc_now()
        records = self._session.scalars(
            select(models.ApprovalTask)
            .join(models.Quotation)
            .where(
                models.Quotation.quotation_id == quotation_id,
                models.ApprovalTask.status.in_(self.OPEN_STATUSES),
            )
        ).all()
        cancelled: list[int] = []
        for record in records:
            record.status = "cancelled_stale"
            record.decision = "cancelled_stale"
            record.reason = reason
            record.completed_at = timestamp
            record.decided_at = timestamp
            cancelled.append(record.id)
        if cancelled:
            self._session.flush()
        return tuple(cancelled)

    # -- reminder scheduling -------------------------------------------

    def list_due_reminders(
        self,
        *,
        now: datetime,
        max_reminders: int = 1,
        limit: int = 50,
    ) -> tuple[ApprovalTaskDTO, ...]:
        """Return pending tasks whose reminder is due and not yet exhausted."""

        statement = (
            select(models.ApprovalTask)
            .where(
                models.ApprovalTask.status.in_(self.OPEN_STATUSES),
                models.ApprovalTask.reminder_due_at.is_not(None),
                models.ApprovalTask.reminder_due_at <= now,
                models.ApprovalTask.reminder_sent_count < max_reminders,
            )
            .order_by(models.ApprovalTask.reminder_due_at)
            .limit(limit)
        )
        return tuple(
            _approval_task_dto(record)
            for record in self._session.scalars(statement)
        )

    def claim_reminder(
        self, *, task_id: int, now: datetime, max_reminders: int = 1
    ) -> ApprovalTaskDTO | None:
        """Lock and claim one task for reminder processing.

        Returns ``None`` when the task is no longer eligible, so a second
        worker observing the same row cannot send a duplicate reminder.
        """

        statement = select(models.ApprovalTask).where(
            models.ApprovalTask.id == task_id
        )
        if (
            self._session.bind is not None
            and self._session.bind.dialect.name != "sqlite"
        ):
            statement = statement.with_for_update(skip_locked=True)
        record = self._session.scalars(statement).one_or_none()
        if record is None:
            return None
        if record.status not in self.OPEN_STATUSES:
            return None
        if record.reminder_due_at is None or record.reminder_due_at > now:
            return None
        if record.reminder_sent_count >= max_reminders:
            return None
        record.reminder_claimed_at = now
        record.reminder_cycle = record.reminder_sent_count + 1
        self._session.flush()
        return _approval_task_dto(record)

    def record_reminder_outcome(
        self,
        *,
        task_id: int,
        sent: bool,
        moment: datetime,
        error_category: str = "",
        next_due_at: datetime | None = None,
    ) -> ApprovalTaskDTO | None:
        """Persist the outcome of one reminder attempt."""

        record = self._session.get(models.ApprovalTask, task_id)
        if record is None:
            return None
        record.reminder_attempt_count += 1
        record.reminder_claimed_at = None
        record.reminder_last_error_category = error_category
        if sent:
            record.reminder_sent_count += 1
            record.reminder_last_sent_at = moment
            record.reminder_due_at = next_due_at
        elif next_due_at is not None:
            record.reminder_due_at = next_due_at
        self._session.flush()
        return _approval_task_dto(record)

    def set_reminder_due_at(
        self, *, task_id: int, due_at: datetime | None
    ) -> None:
        record = self._session.get(models.ApprovalTask, task_id)
        if record is not None:
            record.reminder_due_at = due_at
            self._session.flush()

    def record_action(
        self,
        *,
        task_id: int,
        action_id: str,
        action: str,
        from_status: str,
        to_status: str,
        actor_name: str = "",
        actor_role: str = "",
        actor_user_id: int | None = None,
        reason: str = "",
        original_unit_price: Any | None = None,
        final_unit_price: Any | None = None,
        triggered_rule_ids: tuple[str, ...] = (),
        occurred_at: datetime | None = None,
        quotation_version: int = 0,
    ) -> int:
        task = self._session.get(models.ApprovalTask, task_id)
        if task is None:
            raise QuotationNotFoundError(f"Unknown approval task: {task_id}")

        timestamp = occurred_at or utc_now()
        record = models.ApprovalAction(
            approval_task_id=task_id,
            action_id=action_id,
            action=action,
            actor_user_id=actor_user_id,
            actor_name=actor_name,
            actor_role=actor_role,
            reason=reason,
            from_status=from_status,
            to_status=to_status,
            original_unit_price=_to_decimal(original_unit_price),
            final_unit_price=_to_decimal(final_unit_price),
            triggered_rule_ids=list(triggered_rule_ids),
            occurred_at=timestamp,
            quotation_version=quotation_version or task.quotation_version,
        )
        self._session.add(record)
        try:
            self._session.flush()
        except IntegrityError as error:
            raise DuplicateApprovalActionError(
                f"Approval action {action_id!r} has already been recorded."
            ) from error

        task.status = to_status
        task.decision = action
        task.reason = reason
        task.decided_at = timestamp
        task.completed_at = timestamp
        self._session.flush()
        return record.id

    def record_override(
        self,
        *,
        task_id: int,
        approval_action_id: int | None,
        original_decision: str,
        evaluated_margin_percent: str,
        policy_threshold_percent: str,
        policy_version_id: str,
        approver_name: str,
        approver_role: str,
        justification: str,
        approver_user_id: int | None = None,
        final_approved_price: Any | None = None,
        final_margin_percent: str = "",
        triggered_rule_ids: tuple[str, ...] = (),
        occurred_at: datetime | None = None,
    ) -> int:
        record = models.ApprovalOverrideRecord(
            approval_task_id=task_id,
            approval_action_id=approval_action_id,
            original_decision=original_decision,
            evaluated_margin_percent=evaluated_margin_percent,
            policy_threshold_percent=policy_threshold_percent,
            policy_version_id=policy_version_id,
            approver_user_id=approver_user_id,
            approver_name=approver_name,
            approver_role=approver_role,
            justification=justification,
            final_approved_price=_to_decimal(final_approved_price),
            final_margin_percent=final_margin_percent,
            triggered_rule_ids=list(triggered_rule_ids),
            occurred_at=occurred_at or utc_now(),
        )
        self._session.add(record)
        self._session.flush()
        return record.id


class SqlAlchemyEmailRepository:
    """Persistence for composed emails and their delivery outcomes."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_idempotency_key(self, key: str) -> EmailRecordDTO | None:
        record = self._session.scalars(
            select(models.EmailRecord).where(
                models.EmailRecord.idempotency_key == key
            )
        ).one_or_none()
        return None if record is None else _email_record_dto(record)

    def get(self, email_record_id: int) -> EmailRecordDTO | None:
        record = self._session.get(models.EmailRecord, email_record_id)
        return None if record is None else _email_record_dto(record)

    def create(
        self,
        *,
        quotation_id: str,
        email_type: str,
        audience: str,
        sender: str,
        recipients: tuple[str, ...],
        subject: str,
        body: str = "",
        body_hash: str = "",
        body_storage_mode: str = "hash",
        cc_recipients: tuple[str, ...] = (),
        bcc_recipients: tuple[str, ...] = (),
        quotation_version: int = 0,
        approval_task_id: int | None = None,
        template_version: str = "v1",
        agent_provider: str = "deterministic",
        agent_fallback_used: bool = True,
        agent_fallback_reason: str = "",
        delivery_provider: str = "console",
        status: str = "drafted",
        idempotency_key: str = "",
        attachment_document_ids: tuple[int, ...] = (),
        reminder_cycle: int = 0,
        created_by_user_id: int | None = None,
    ) -> EmailRecordDTO:
        parent = self._session.scalars(
            select(models.Quotation).where(
                models.Quotation.quotation_id == quotation_id
            )
        ).one_or_none()
        if parent is None:
            raise QuotationNotFoundError(f"Unknown quotation: {quotation_id}")
        record = models.EmailRecord(
            email_id=f"EMAIL-{uuid4().hex[:16].upper()}",
            quotation_id=parent.id,
            quotation_reference=quotation_id,
            quotation_version=quotation_version or parent.version,
            approval_task_id=approval_task_id,
            email_type=email_type,
            audience=audience,
            sender=sender,
            recipients=list(recipients),
            cc_recipients=list(cc_recipients),
            bcc_recipients=list(bcc_recipients),
            subject=subject,
            body=body,
            body_hash=body_hash,
            body_storage_mode=body_storage_mode,
            template_version=template_version,
            agent_provider=agent_provider,
            agent_fallback_used=agent_fallback_used,
            agent_fallback_reason=agent_fallback_reason,
            delivery_provider=delivery_provider,
            status=status,
            idempotency_key=idempotency_key,
            attachment_document_ids=list(attachment_document_ids),
            reminder_cycle=reminder_cycle,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(record)
        try:
            self._session.flush()
        except IntegrityError as error:
            raise RepositoryError(
                "An email with this idempotency key already exists."
            ) from error
        return _email_record_dto(record)

    def record_attempt(
        self,
        *,
        email_record_id: int,
        status: str,
        moment: datetime | None = None,
        error_category: str = "none",
        error_detail: str = "",
        provider_message_id: str = "",
        increment_attempt: bool = True,
    ) -> EmailRecordDTO | None:
        record = self._session.get(models.EmailRecord, email_record_id)
        if record is None:
            return None
        if increment_attempt:
            record.attempt_count += 1
        record.status = status
        record.last_error_category = error_category
        record.last_error_detail = error_detail
        if provider_message_id:
            record.provider_message_id = provider_message_id
        if status == "sent":
            record.sent_at = moment or utc_now()
        self._session.flush()
        return _email_record_dto(record)

    def list_for_quotation(
        self, quotation_id: str, *, email_type: str | None = None
    ) -> tuple[EmailRecordDTO, ...]:
        statement = (
            select(models.EmailRecord)
            .where(models.EmailRecord.quotation_reference == quotation_id)
            .order_by(models.EmailRecord.id)
        )
        if email_type is not None:
            statement = statement.where(
                models.EmailRecord.email_type == email_type
            )
        return tuple(
            _email_record_dto(record)
            for record in self._session.scalars(statement)
        )

    def list_by_status(
        self, statuses: tuple[str, ...], *, limit: int = 100
    ) -> tuple[EmailRecordDTO, ...]:
        statement = (
            select(models.EmailRecord)
            .where(models.EmailRecord.status.in_(statuses))
            .order_by(models.EmailRecord.id)
            .limit(limit)
        )
        return tuple(
            _email_record_dto(record)
            for record in self._session.scalars(statement)
        )


class SqlAlchemyDocumentRepository:
    """Read and write generated documents used as email attachments."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        quotation_id: str,
        kind: str,
        audience: str,
        filename: str,
        mime_type: str,
        content: bytes,
        quotation_version: int = 0,
        generated_by_user_id: int | None = None,
    ) -> int:
        parent = self._session.scalars(
            select(models.Quotation).where(
                models.Quotation.quotation_id == quotation_id
            )
        ).one_or_none()
        if parent is None:
            raise QuotationNotFoundError(f"Unknown quotation: {quotation_id}")
        record = models.GeneratedDocument(
            quotation_id=parent.id,
            quotation_version=quotation_version or parent.version,
            kind=kind,
            audience=audience,
            filename=filename,
            mime_type=mime_type,
            content=content,
            byte_size=len(content),
            checksum=sha256(content).hexdigest(),
            generated_by_user_id=generated_by_user_id,
        )
        self._session.add(record)
        self._session.flush()
        return record.id

    def latest_for_version(
        self, *, quotation_id: str, quotation_version: int, kind: str
    ) -> models.GeneratedDocument | None:
        return self._session.scalars(
            select(models.GeneratedDocument)
            .join(models.Quotation)
            .where(
                models.Quotation.quotation_id == quotation_id,
                models.GeneratedDocument.quotation_version == quotation_version,
                models.GeneratedDocument.kind == kind,
            )
            .order_by(models.GeneratedDocument.id.desc())
        ).first()

    def get(self, document_id: int) -> models.GeneratedDocument | None:
        return self._session.get(models.GeneratedDocument, document_id)


# -- ORM to DTO mapping ------------------------------------------------


def _approval_task_dto(record: models.ApprovalTask) -> ApprovalTaskDTO:
    return ApprovalTaskDTO(
        id=record.id,
        task_reference=record.task_reference,
        quotation_reference=record.quotation_reference,
        quotation_version=record.quotation_version,
        decision_status=record.decision_status,
        status=record.status,
        assigned_user_id=record.assigned_user_id,
        assigned_approver_name=record.assigned_approver_name,
        assigned_approver_role=record.assigned_approver_role,
        submitted_by_user_id=record.submitted_by_user_id,
        submitted_at=record.submitted_at,
        reminder_due_at=record.reminder_due_at,
        completed_at=record.completed_at,
        policy_version_id=record.policy_version_id,
        pricing_run_id=record.pricing_run_id,
        validation_run_id=record.validation_run_id,
        decision=record.decision,
        reason=record.reason,
        reminder_cycle=record.reminder_cycle,
        reminder_sent_count=record.reminder_sent_count,
        reminder_last_sent_at=record.reminder_last_sent_at,
        reminder_last_error_category=record.reminder_last_error_category,
        reminder_attempt_count=record.reminder_attempt_count,
    )


def _user_dto(record: models.User) -> UserDTO:
    return UserDTO(
        id=record.id,
        username=record.username,
        display_name=record.display_name,
        email=record.email,
        roles=tuple(record.roles or ()),
        is_active=record.is_active,
    )


def _line_item_dto(record: models.QuotationLineItem) -> LineItemDTO:
    return LineItemDTO(
        id=record.id,
        position=record.position,
        item_type=LineItemType(record.item_type),
        product_id=record.product_id,
        customer_description=record.customer_description,
        internal_description=record.internal_description,
        quantity=record.quantity,
        currency=record.currency,
        proposed_unit_price=_to_decimal(record.proposed_unit_price),
        approved_unit_price=_to_decimal(record.approved_unit_price),
        list_unit_price=_to_decimal(record.list_unit_price),
        discount_percent=_to_decimal(record.discount_percent),
        is_optional=record.is_optional,
    )


def _audit_dto(record: models.AuditEventRecord) -> AuditEventDTO:
    return AuditEventDTO(
        event_type=record.event_type,
        actor=record.actor,
        occurred_at=record.occurred_at,
        quotation_reference=record.quotation_reference,
        before_state=record.before_state,
        after_state=record.after_state,
        changed_fields=tuple(record.changed_fields or ()),
        reason=record.reason,
        triggered_rule_ids=tuple(record.triggered_rule_ids or ()),
        details=dict(record.details or {}),
        actor_role=record.actor_role,
        quotation_version=record.quotation_version,
        policy_version_id=record.policy_version_id,
        request_id=record.request_id,
    )


def _summary_dto(record: models.Quotation) -> QuotationSummaryDTO:
    return QuotationSummaryDTO(
        id=record.id,
        quotation_id=record.quotation_id,
        customer_name=record.customer_name,
        region=record.region,
        currency=record.currency,
        status=record.status,
        approval_status=record.approval_status,
        version=record.version,
        is_closed=record.is_closed,
        owner_user_id=record.owner_user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _quotation_dto(record: models.Quotation) -> QuotationDTO:
    return QuotationDTO(
        id=record.id,
        quotation_id=record.quotation_id,
        customer_name=record.customer_name,
        customer_type=record.customer_type,
        region=record.region,
        currency=record.currency,
        incoterm=record.incoterm,
        delivery_location=record.delivery_location,
        status=record.status,
        approval_status=record.approval_status,
        is_closed=record.is_closed,
        version=record.version,
        owner_user_id=record.owner_user_id,
        pricing_data_version_id=record.pricing_data_version_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        line_items=tuple(
            _line_item_dto(item)
            for item in sorted(record.line_items, key=lambda row: row.position)
        ),
        audit_events=tuple(
            _audit_dto(item)
            for item in sorted(record.audit_events, key=lambda row: row.id)
        ),
        state_document=dict(record.state_document or {}),
    )


def _email_record_dto(record: models.EmailRecord) -> EmailRecordDTO:
    return EmailRecordDTO(
        id=record.id,
        email_id=record.email_id,
        quotation_reference=record.quotation_reference,
        quotation_version=record.quotation_version,
        email_type=record.email_type,
        audience=record.audience,
        sender=record.sender,
        recipients=tuple(record.recipients or ()),
        cc_recipients=tuple(record.cc_recipients or ()),
        bcc_recipients=tuple(record.bcc_recipients or ()),
        subject=record.subject,
        body=record.body,
        body_hash=record.body_hash,
        body_storage_mode=record.body_storage_mode,
        template_version=record.template_version,
        agent_provider=record.agent_provider,
        agent_fallback_used=record.agent_fallback_used,
        agent_fallback_reason=record.agent_fallback_reason,
        delivery_provider=record.delivery_provider,
        status=record.status,
        attempt_count=record.attempt_count,
        approval_task_id=record.approval_task_id,
        created_at=record.created_at,
        sent_at=record.sent_at,
        last_error_category=record.last_error_category,
        last_error_detail=record.last_error_detail,
        idempotency_key=record.idempotency_key,
        provider_message_id=record.provider_message_id,
        attachment_document_ids=tuple(record.attachment_document_ids or ()),
        reminder_cycle=record.reminder_cycle,
    )
