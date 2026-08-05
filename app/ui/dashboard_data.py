"""Read-only dashboard projections.

Every number here is derived from the existing service layer: no new business
rule, no new query semantics. The projection exists so the dashboard page
stays a thin renderer and so the counts can be tested without Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.auth.provider import AuthenticatedUser, PermissionDeniedError
from app.auth.roles import Permission
from app.domain.dto import AuditEventDTO, ApprovalTaskDTO, QuotationSummaryDTO
from app.services.approval_service import ApprovalService
from app.services.audit_view import AuditViewService
from app.services.quotation_service import QuotationService

__all__ = ["DashboardData", "build_dashboard"]

#: Quotation statuses that count as "in flight" for the sales dashboard.
_CLOSED_APPROVAL_STATUSES = frozenset({"approved", "approved_with_override", "rejected"})


@dataclass(frozen=True)
class DashboardData:
    """Everything the dashboard renders for one signed-in user."""

    active_quotations: tuple[QuotationSummaryDTO, ...] = ()
    my_quotations: tuple[QuotationSummaryDTO, ...] = ()
    pending_tasks: tuple[ApprovalTaskDTO, ...] = ()
    completed_tasks: tuple[ApprovalTaskDTO, ...] = ()
    recent_events: tuple[AuditEventDTO, ...] = ()
    notices: tuple[str, ...] = field(default_factory=tuple)

    @property
    def active_quotation_count(self) -> int:
        return len(self.active_quotations)

    @property
    def pending_task_count(self) -> int:
        return len(self.pending_tasks)


def _is_active(summary: QuotationSummaryDTO) -> bool:
    if summary.is_closed:
        return False
    return summary.approval_status not in _CLOSED_APPROVAL_STATUSES


def _sorted_by_recency(
    summaries: tuple[QuotationSummaryDTO, ...],
) -> tuple[QuotationSummaryDTO, ...]:
    return tuple(
        sorted(summaries, key=lambda item: item.updated_at, reverse=True)
    )


def build_dashboard(
    user: AuthenticatedUser,
    *,
    quotations: QuotationService | None = None,
    approvals: ApprovalService | None = None,
    audit: AuditViewService | None = None,
    activity_limit: int = 10,
) -> DashboardData:
    """Assemble the dashboard projection for ``user``.

    Each section is attempted independently: a role without a permission
    simply gets an empty section and an explanatory notice, never an error
    page.
    """

    quotation_service = quotations or QuotationService()
    approval_service = approvals or ApprovalService()
    audit_service = audit or AuditViewService()

    notices: list[str] = []
    mine: tuple[QuotationSummaryDTO, ...] = ()
    if user.has_permission(Permission.VIEW_OWN_QUOTATIONS):
        owner = (
            None
            if user.has_permission(Permission.VIEW_APPROVAL_TASKS)
            else user.user_id
        )
        mine = _sorted_by_recency(
            quotation_service.list_quotations(owner_user_id=owner)
        )
    else:
        notices.append("Your role does not include quotation visibility.")

    pending: tuple[ApprovalTaskDTO, ...] = ()
    completed: tuple[ApprovalTaskDTO, ...] = ()
    if user.has_permission(Permission.VIEW_APPROVAL_TASKS):
        try:
            pending = approval_service.list_tasks(user, only_open=True)
            every = approval_service.list_tasks(
                user, only_open=False, assigned_to_me=False
            )
        except PermissionDeniedError:
            pending = ()
            every = ()
        pending_ids = {task.id for task in pending}
        completed = tuple(
            task
            for task in every
            if task.id not in pending_ids and task.completed_at is not None
        )

    events: tuple[AuditEventDTO, ...] = ()
    if user.has_permission(Permission.VIEW_AUDIT_RECORDS):
        try:
            events = audit_service.list_recent(user, limit=activity_limit)
        except PermissionDeniedError:
            events = ()
    else:
        events = _activity_from_quotations(mine, limit=activity_limit)

    return DashboardData(
        active_quotations=tuple(item for item in mine if _is_active(item)),
        my_quotations=mine,
        pending_tasks=pending,
        completed_tasks=completed,
        recent_events=events,
        notices=tuple(notices),
    )


def _activity_from_quotations(
    summaries: tuple[QuotationSummaryDTO, ...], *, limit: int
) -> tuple[AuditEventDTO, ...]:
    """Derive a recent-activity list for roles without audit access.

    No audit record is read here; the entries are a presentation-only summary
    of the user's own quotations.
    """

    entries: list[AuditEventDTO] = []
    for summary in summaries[:limit]:
        entries.append(
            AuditEventDTO(
                event_type=summary.status or "quotation_updated",
                actor="",
                occurred_at=_as_datetime(summary.updated_at),
                quotation_reference=summary.quotation_id,
                after_state=summary.approval_status,
                quotation_version=summary.version,
            )
        )
    return tuple(entries)


def _as_datetime(value: datetime) -> datetime:
    return value
