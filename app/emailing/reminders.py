"""The persistent approval-reminder worker.

The worker is a plain process: it queries the database for due reminders,
claims each task inside a transaction, rechecks its status, sends at most one
reminder per configured cycle, and persists the outcome. Because every piece
of state lives in the database, a web-process restart cannot lose a pending
reminder, and two concurrent worker runs cannot send the same reminder twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Mapping

from sqlalchemy.orm import Session, sessionmaker

from app.emailing.config import EmailConfig, load_email_config
from app.emailing.contracts import (
    DeliveryErrorCategory,
    EmailError,
    EmailNotAllowedError,
    EmailStatus,
    PERMANENT_ERROR_CATEGORIES,
    RecipientValidationError,
)
from app.emailing.service import EmailService, TASK_STATUS_PENDING
from app.quotation_models import utc_now

#: Delay before a transient failure is retried.
DEFAULT_RETRY_BACKOFF_MINUTES = 30.0


@dataclass
class ReminderRunReport:
    """What one worker run did. Used by the CLI and by the tests."""

    considered: int = 0
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    retry_scheduled: int = 0
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "considered": self.considered,
            "sent": self.sent,
            "skipped": self.skipped,
            "failed": self.failed,
            "retry_scheduled": self.retry_scheduled,
            "details": list(self.details),
        }


class ApprovalReminderWorker:
    """Database-backed due-task processing for approval reminders."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        email_service: EmailService | None = None,
        config: EmailConfig | None = None,
        environment: Mapping[str, str] | None = None,
        retry_backoff_minutes: float = DEFAULT_RETRY_BACKOFF_MINUTES,
    ) -> None:
        self.config = config or load_email_config(environment)
        self._session_factory = session_factory
        self.emails = email_service or EmailService(
            session_factory, config=self.config, environment=environment
        )
        self._retry_backoff = timedelta(minutes=retry_backoff_minutes)

    def _unit_of_work(self):
        from app.services.unit_of_work import UnitOfWork

        return UnitOfWork(self._session_factory)

    def run_once(self, *, now: datetime | None = None) -> ReminderRunReport:
        """Process every due reminder exactly once."""

        moment = now or utc_now()
        report = ReminderRunReport()
        with self._unit_of_work() as uow:
            due = uow.approvals.list_due_reminders(
                now=moment, max_reminders=self.config.reminder_max_count
            )
        for candidate in due:
            report.considered += 1
            self._process(candidate.id, moment, report)
        return report

    def _process(
        self, task_id: int, moment: datetime, report: ReminderRunReport
    ) -> None:
        # Claim first. A task that is completed, rejected, revised, cancelled,
        # stale or already reminded in this cycle is not claimable.
        with self._unit_of_work() as uow:
            claimed = uow.approvals.claim_reminder(
                task_id=task_id,
                now=moment,
                max_reminders=self.config.reminder_max_count,
            )
            if claimed is None:
                report.skipped += 1
                report.details.append(f"task {task_id}: not eligible")
                uow.rollback()
                return
            uow.commit()

        cycle = claimed.reminder_cycle
        try:
            record = self.emails.send_reminder(claimed, reminder_cycle=cycle)
        except (EmailNotAllowedError, RecipientValidationError) as error:
            self._record_outcome(
                task_id,
                sent=False,
                moment=moment,
                error_category=DeliveryErrorCategory.PERMANENT.value,
                next_due_at=None,
            )
            report.skipped += 1
            report.details.append(f"task {task_id}: {error}")
            return
        except EmailError as error:
            self._record_outcome(
                task_id,
                sent=False,
                moment=moment,
                error_category=DeliveryErrorCategory.PERMANENT.value,
                next_due_at=None,
            )
            report.failed += 1
            report.details.append(f"task {task_id}: {error}")
            return

        if record.status == EmailStatus.SENT.value:
            self._record_outcome(
                task_id,
                sent=True,
                moment=moment,
                error_category="",
                next_due_at=None,
            )
            report.sent += 1
            report.details.append(
                f"task {task_id}: reminder sent (cycle {cycle})"
            )
            return

        category = record.last_error_category
        transient = category == DeliveryErrorCategory.TRANSIENT.value
        attempts_left = record.attempt_count < self.config.max_delivery_attempts
        if transient and attempts_left:
            self._record_outcome(
                task_id,
                sent=False,
                moment=moment,
                error_category=category,
                next_due_at=moment + self._retry_backoff,
            )
            report.retry_scheduled += 1
            report.details.append(
                f"task {task_id}: transient failure, retry scheduled"
            )
            return

        # Permanent failures, and exhausted retries, stop the cycle.
        self._record_outcome(
            task_id,
            sent=False,
            moment=moment,
            error_category=category
            or DeliveryErrorCategory.PERMANENT.value,
            next_due_at=None,
        )
        report.failed += 1
        report.details.append(
            f"task {task_id}: delivery failed ({category or 'unknown'})"
        )

    def _record_outcome(
        self,
        task_id: int,
        *,
        sent: bool,
        moment: datetime,
        error_category: str,
        next_due_at: datetime | None,
    ) -> None:
        with self._unit_of_work() as uow:
            uow.approvals.record_reminder_outcome(
                task_id=task_id,
                sent=sent,
                moment=moment,
                error_category=error_category,
                next_due_at=next_due_at,
            )
            uow.commit()


def permanent_categories() -> frozenset[str]:
    return frozenset(category.value for category in PERMANENT_ERROR_CATEGORIES)


__all__ = [
    "ApprovalReminderWorker",
    "ReminderRunReport",
    "TASK_STATUS_PENDING",
    "permanent_categories",
]
