"""Phase 7 integration: the persistent two-day approval reminder."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.emailing.contracts import DeliveryErrorCategory, EmailStatus, EmailType
from app.emailing.reminders import ApprovalReminderWorker
from app.emailing.service import EmailService
from app.quotation_models import utc_now
from app.services.unit_of_work import UnitOfWork
from tests.fixtures.phase7_helpers import FailingProvider, email_config, submit_quotation

OVERRIDE_REASON = (
    "Strategic account. I acknowledge the quotation margin is equal to or "
    "below the configured policy threshold and accept the commercial risk."
)


def _task(session_factory, task_id):
    with UnitOfWork(session_factory) as uow:
        return uow.approvals.get_task(task_id)


def test_submission_persists_a_two_day_reminder_due_time(
    service, approval_service, people, session_factory
):
    task = submit_quotation(service, approval_service, people, "Q7-RM-1")
    stored = _task(session_factory, task.id)
    assert stored.reminder_due_at is not None
    delta = stored.reminder_due_at - stored.submitted_at
    assert timedelta(hours=47, minutes=59) < delta < timedelta(hours=48, minutes=1)


def test_the_reminder_delay_is_configurable(
    service, approval_service, people, session_factory, monkeypatch
):
    monkeypatch.setenv("APPROVAL_REMINDER_DELAY_HOURS", "6")
    task = submit_quotation(service, approval_service, people, "Q7-RM-2")
    stored = _task(session_factory, task.id)
    delta = stored.reminder_due_at - stored.submitted_at
    assert timedelta(hours=5, minutes=59) < delta < timedelta(hours=6, minutes=1)


def test_no_reminder_is_sent_before_the_due_time(
    service, approval_service, people, reminder_worker, email_provider
):
    submit_quotation(service, approval_service, people, "Q7-RM-3")
    report = reminder_worker.run_once(now=utc_now() + timedelta(hours=47))
    assert report.considered == 0
    assert report.sent == 0
    assert email_provider.sent == []


def test_one_reminder_is_sent_once_the_task_is_due(
    service, approval_service, people, reminder_worker, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-RM-4")
    report = reminder_worker.run_once(now=utc_now() + timedelta(hours=49))

    assert report.sent == 1
    key, message = email_provider.sent[0]
    assert key.endswith("|two_day_pending_approval|1")
    assert str(task.id) in key
    assert "Reminder" in message.subject
    assert "Original decision: pass" in message.body
    assert "Reminder due at:" in message.body


def test_a_second_worker_run_does_not_duplicate_the_reminder(
    service, approval_service, people, reminder_worker, email_provider
):
    submit_quotation(service, approval_service, people, "Q7-RM-5")
    moment = utc_now() + timedelta(hours=49)
    reminder_worker.run_once(now=moment)
    second = reminder_worker.run_once(now=moment + timedelta(minutes=5))

    assert second.sent == 0
    assert len(email_provider.sent) == 1


def test_two_worker_instances_do_not_duplicate_the_reminder(
    service,
    approval_service,
    people,
    session_factory,
    email_service,
    email_config,
    email_provider,
):
    submit_quotation(service, approval_service, people, "Q7-RM-6")
    moment = utc_now() + timedelta(hours=49)
    first = ApprovalReminderWorker(
        session_factory, email_service=email_service, config=email_config
    )
    second = ApprovalReminderWorker(
        session_factory, email_service=email_service, config=email_config
    )
    first.run_once(now=moment)
    second.run_once(now=moment)
    assert len(email_provider.sent) == 1


def test_a_reminder_is_skipped_after_approval(
    service, approval_service, people, reminder_worker, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-RM-7")
    approval_service.act(user=people["manager"], task_id=task.id, action="approve")
    report = reminder_worker.run_once(now=utc_now() + timedelta(hours=49))
    assert report.sent == 0
    assert email_provider.sent == []


def test_a_reminder_is_skipped_after_a_revision_request(
    service, approval_service, people, reminder_worker, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-RM-8")
    approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="request_revision",
        reason="Please confirm the installation scope with the customer.",
    )
    report = reminder_worker.run_once(now=utc_now() + timedelta(hours=49))
    assert report.sent == 0
    assert email_provider.sent == []


def test_a_reminder_is_skipped_after_rejection(
    service, approval_service, people, reminder_worker, email_provider
):
    task = submit_quotation(
        service,
        approval_service,
        people,
        "Q7-RM-9",
        status="review_required",
        margin="15.0",
    )
    approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="reject",
        reason="The commercial terms cannot be supported.",
    )
    report = reminder_worker.run_once(now=utc_now() + timedelta(hours=49))
    assert report.sent == 0


def test_a_reminder_is_skipped_after_a_stale_task_is_cancelled(
    service, approval_service, people, reminder_worker, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-RM-10")
    loaded = service.load_quotation("Q7-RM-10")
    loaded.state.draft.customer_name = "Northwind Medical Group"
    loaded = service.save_state(loaded, actor=people["sales"].username)
    approval_service.cancel_open_tasks_for_material_edit(
        "Q7-RM-10", user=people["sales"], quotation_version=loaded.version
    )

    report = reminder_worker.run_once(now=utc_now() + timedelta(hours=49))
    assert report.sent == 0
    assert email_provider.sent == []


def test_a_transient_failure_is_retried_and_then_succeeds(
    service, approval_service, people, session_factory, email_config
):
    submit_quotation(service, approval_service, people, "Q7-RM-11")
    provider = FailingProvider(failures=1)
    emails = EmailService(
        session_factory, config=email_config, provider=provider
    )
    worker = ApprovalReminderWorker(
        session_factory,
        email_service=emails,
        config=email_config,
        retry_backoff_minutes=30.0,
    )
    moment = utc_now() + timedelta(hours=49)
    first = worker.run_once(now=moment)
    assert first.retry_scheduled == 1
    assert provider.sent == []

    second = worker.run_once(now=moment + timedelta(minutes=31))
    assert second.sent == 1
    assert len(provider.sent) == 1


def test_a_permanent_failure_is_recorded_and_not_retried(
    service, approval_service, people, session_factory, email_config
):
    submit_quotation(service, approval_service, people, "Q7-RM-12")
    provider = FailingProvider(
        failures=5, category=DeliveryErrorCategory.PERMANENT
    )
    emails = EmailService(
        session_factory, config=email_config, provider=provider
    )
    worker = ApprovalReminderWorker(
        session_factory, email_service=emails, config=email_config
    )
    moment = utc_now() + timedelta(hours=49)
    first = worker.run_once(now=moment)
    assert first.failed == 1

    second = worker.run_once(now=moment + timedelta(hours=2))
    assert second.sent == 0
    assert provider.attempts == 1

    with UnitOfWork(session_factory) as uow:
        records = uow.emails.list_for_quotation("Q7-RM-12")
    reminder = [
        record
        for record in records
        if record.email_type == EmailType.APPROVAL_REMINDER.value
    ][0]
    assert reminder.status == EmailStatus.FAILED.value
    assert reminder.last_error_category == DeliveryErrorCategory.PERMANENT.value


def test_reminder_state_survives_a_process_restart(
    service, approval_service, people, session_factory, email_config, email_provider
):
    """A brand new worker object reads its state from the database only."""

    submit_quotation(service, approval_service, people, "Q7-RM-13")
    moment = utc_now() + timedelta(hours=49)

    restarted = ApprovalReminderWorker(
        session_factory,
        email_service=EmailService(
            session_factory, config=email_config, provider=email_provider
        ),
        config=email_config,
    )
    assert restarted.run_once(now=moment).sent == 1

    restarted_again = ApprovalReminderWorker(
        session_factory,
        email_service=EmailService(
            session_factory, config=email_config, provider=email_provider
        ),
        config=email_config,
    )
    assert restarted_again.run_once(now=moment + timedelta(hours=1)).sent == 0


def test_the_configured_reminder_count_is_never_exceeded(
    service, approval_service, people, session_factory, email_provider
):
    submit_quotation(service, approval_service, people, "Q7-RM-14")
    config = email_config(reminder_max_count=2)
    worker = ApprovalReminderWorker(
        session_factory,
        email_service=EmailService(
            session_factory, config=config, provider=email_provider
        ),
        config=config,
    )
    moment = utc_now() + timedelta(hours=49)
    for hours in (0, 49, 98, 147):
        worker.run_once(now=moment + timedelta(hours=hours))
    assert len(email_provider.sent) <= 2


def test_the_worker_cli_supports_run_once(monkeypatch):
    from worker import reminder_worker as cli

    calls: list[str] = []

    class Stub:
        def __init__(self, *args, **kwargs):
            pass

        def run_once(self, *, now=None):
            from app.emailing.reminders import ReminderRunReport

            calls.append("run")
            return ReminderRunReport()

    monkeypatch.setattr(cli, "ApprovalReminderWorker", Stub)
    assert cli.main(["--run-once"]) == 0
    assert calls == ["run"]
