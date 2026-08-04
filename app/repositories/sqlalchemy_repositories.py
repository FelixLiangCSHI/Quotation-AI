"""SQLAlchemy implementations of the repository interfaces.

Repositories never commit. The transaction boundary belongs to the service
layer's unit of work, so a service failure rolls back every write it made.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import models
from app.domain.dto import (
    AuditEventDTO,
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
    ) -> UserDTO:
        record = models.User(
            username=username,
            display_name=display_name or username,
            email=email,
            roles=list(roles),
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
            actor_user_id=actor_user_id,
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
            assigned_user_id=assigned_user_id,
            assigned_approver_name=assigned_approver_name,
            assigned_approver_role=assigned_approver_role,
            status="pending_review",
            due_at=due_at,
            reminder_due_at=reminder_due_at,
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
        self._session.flush()
        return record.id


# -- ORM to DTO mapping ------------------------------------------------


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
