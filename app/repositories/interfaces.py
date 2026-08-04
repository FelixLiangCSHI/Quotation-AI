"""Repository interfaces.

These protocols are the contract between the service layer and persistence.
Services depend on them, never on SQLAlchemy directly, so an alternative
backing store can be substituted without touching business logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.domain.dto import (
    AuditEventDTO,
    LineItemDTO,
    QuotationDTO,
    QuotationSummaryDTO,
    UserDTO,
)


class RepositoryError(RuntimeError):
    """Base class for repository failures."""


class QuotationNotFoundError(RepositoryError):
    """Raised when a quotation cannot be located."""


class QuotationVersionConflictError(RepositoryError):
    """Raised when a write is based on a stale quotation version.

    This is the explicit mechanism that prevents one user's edit from silently
    overwriting another's.
    """

    def __init__(
        self,
        quotation_id: str,
        expected_version: int,
        actual_version: int,
    ) -> None:
        super().__init__(
            f"Quotation {quotation_id} was modified by another user "
            f"(expected version {expected_version}, found {actual_version}). "
            "Reload the quotation and reapply your changes."
        )
        self.quotation_id = quotation_id
        self.expected_version = expected_version
        self.actual_version = actual_version


class DuplicateApprovalActionError(RepositoryError):
    """Raised when an approval action id has already been recorded."""


@runtime_checkable
class UserRepository(Protocol):
    def add(
        self,
        *,
        username: str,
        display_name: str = "",
        email: str = "",
        roles: tuple[str, ...] = (),
    ) -> UserDTO: ...

    def get_by_username(self, username: str) -> UserDTO | None: ...

    def get(self, user_id: int) -> UserDTO | None: ...


@runtime_checkable
class QuotationRepository(Protocol):
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
    ) -> QuotationDTO: ...

    def get_by_quotation_id(self, quotation_id: str) -> QuotationDTO | None: ...

    def get(self, primary_key: int) -> QuotationDTO | None: ...

    def list_summaries(
        self,
        *,
        owner_user_id: int | None = None,
        include_closed: bool = True,
    ) -> tuple[QuotationSummaryDTO, ...]: ...

    def update(
        self,
        *,
        quotation_id: str,
        expected_version: int,
        state_document: dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> QuotationDTO: ...

    def replace_line_items(
        self,
        *,
        quotation_id: str,
        expected_version: int,
        line_items: tuple[LineItemDTO, ...],
    ) -> QuotationDTO: ...

    def set_closed(
        self,
        *,
        quotation_id: str,
        expected_version: int,
        is_closed: bool,
    ) -> QuotationDTO: ...


@runtime_checkable
class AuditEventRepository(Protocol):
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
    ) -> AuditEventDTO: ...

    def list_for_quotation(
        self, quotation_id: str
    ) -> tuple[AuditEventDTO, ...]: ...


@runtime_checkable
class ApprovalRepository(Protocol):
    def open_task(
        self,
        *,
        quotation_id: str,
        assigned_approver_name: str = "",
        assigned_approver_role: str = "",
        assigned_user_id: int | None = None,
        due_at: datetime | None = None,
        reminder_due_at: datetime | None = None,
    ) -> int: ...

    def get_open_task_id(self, quotation_id: str) -> int | None: ...

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
    ) -> int: ...
