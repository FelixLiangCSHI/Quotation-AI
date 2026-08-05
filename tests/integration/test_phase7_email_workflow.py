"""Phase 7 integration: approval-request, customer and owner emails."""

from __future__ import annotations

import pytest

from app.auth.provider import PermissionDeniedError
from app.emailing.contracts import (
    EmailNotAllowedError,
    EmailStatus,
    EmailType,
    RecipientValidationError,
)
from app.emailing.service import EmailService, build_idempotency_key
from tests.fixtures.phase7_helpers import (
    CUSTOMER_ADDRESS,
    email_config,
    submit_quotation,
)

OVERRIDE_REASON = (
    "Strategic account. I acknowledge the quotation margin is equal to or "
    "below the configured policy threshold and accept the commercial risk."
)


def _approve(approval_service, people, task, *, override=False):
    if override:
        return approval_service.act(
            user=people["manager"],
            task_id=task.id,
            action="approve_with_override",
            reason=OVERRIDE_REASON,
            acknowledge_below_threshold=True,
        )
    return approval_service.act(
        user=people["manager"], task_id=task.id, action="approve"
    )


# -- approval request ---------------------------------------------------


def test_the_approval_request_goes_to_the_assigned_approver(
    service, approval_service, people, email_service, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-AR-1")
    record = email_service.send_approval_request(task.id, user=people["sales"])

    assert record.status == EmailStatus.SENT.value
    assert record.recipients == ("mia.manager@internal.invalid",)
    assert record.email_type == EmailType.APPROVAL_REQUEST.value
    _, message = email_provider.sent[0]
    assert "Decision: PASS" in message.body
    assert "Q7-AR-1" in message.body


def test_a_review_required_approval_request_states_the_override_options(
    service, approval_service, people, email_service, email_provider
):
    task = submit_quotation(
        service,
        approval_service,
        people,
        "Q7-AR-2",
        status="review_required",
        margin="18.0",
        approver="pricing",
    )
    email_service.send_approval_request(task.id, user=people["sales"])
    _, message = email_provider.sent[0]
    assert "Decision: REVIEW_REQUIRED" in message.body
    # The pricing manager may see commercial detail, so margin is included.
    assert "Gross margin" in message.body


def test_a_sales_manager_approver_does_not_receive_margin_detail(
    service, approval_service, people, email_service, email_provider
):
    task = submit_quotation(
        service,
        approval_service,
        people,
        "Q7-AR-3",
        status="review_required",
        margin="18.0",
        approver="manager",
    )
    email_service.send_approval_request(task.id, user=people["sales"])
    _, message = email_provider.sent[0]
    assert "Gross margin" not in message.body
    assert "Policy threshold" not in message.body


def test_the_approver_address_cannot_be_replaced_by_an_arbitrary_address(
    service, approval_service, people, email_service, session_factory
):
    task = submit_quotation(service, approval_service, people, "Q7-AR-4")
    draft = email_service.compose_approval_request(task.id, user=people["sales"])
    # There is no parameter through which a caller could supply an address;
    # the recipient always comes from the stored approver record.
    assert draft.message.recipients == ("mia.manager@internal.invalid",)

    from app.services.unit_of_work import UnitOfWork

    with UnitOfWork(session_factory) as uow:
        approver = uow.users.get(people["manager"].user_id)
        assert draft.message.recipients == (approver.email,)


def test_an_approver_without_a_stored_address_is_refused(
    service, approval_service, auth_provider, people, email_service, session_factory
):
    from app.auth import Role
    from tests.fixtures.phase6_helpers import create_user

    nomail = create_user(
        auth_provider, "nate.nomail", Role.SALES_MANAGER, email=""
    )
    people = dict(people)
    people["nomail"] = nomail
    task = submit_quotation(
        service, approval_service, people, "Q7-AR-5", approver="nomail"
    )
    with pytest.raises(RecipientValidationError):
        email_service.compose_approval_request(task.id, user=people["sales"])


def test_automatic_sending_requires_explicit_configuration(
    service, approval_service, people, session_factory, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-AR-6")
    default_service = EmailService(
        session_factory, config=email_config(), provider=email_provider
    )
    assert (
        default_service.send_approval_request_on_submission(
            task.id, user=people["sales"]
        )
        is None
    )
    enabled = EmailService(
        session_factory,
        config=email_config(auto_send_approval_request=True),
        provider=email_provider,
    )
    record = enabled.send_approval_request_on_submission(
        task.id, user=people["sales"]
    )
    assert record is not None and record.status == EmailStatus.SENT.value


def test_sending_twice_reuses_the_idempotency_key(
    service, approval_service, people, email_service, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-AR-7")
    first = email_service.send_approval_request(task.id, user=people["sales"])
    second = email_service.send_approval_request(task.id, user=people["sales"])
    assert first.id == second.id
    assert len(email_provider.sent) == 1


# -- customer email -----------------------------------------------------


def test_a_customer_email_cannot_be_composed_before_approval(
    service, approval_service, people, email_service
):
    submit_quotation(service, approval_service, people, "Q7-CU-1")
    with pytest.raises(EmailNotAllowedError):
        email_service.draft_customer_email(
            "Q7-CU-1", user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
        )


def test_the_approved_customer_workflow_succeeds(
    service, approval_service, people, email_service, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-CU-2")
    _approve(approval_service, people, task)

    draft = email_service.draft_customer_email(
        "Q7-CU-2", user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
    )
    assert draft.record.status == EmailStatus.PENDING_REVIEW.value
    assert not email_provider.sent

    with pytest.raises(EmailNotAllowedError):
        email_service.send_reviewed_customer_email(
            draft, user=people["sales"], draft_approved=False
        )

    record = email_service.send_reviewed_customer_email(
        draft, user=people["sales"], draft_approved=True
    )
    assert record.status == EmailStatus.SENT.value
    assert record.recipients == (CUSTOMER_ADDRESS,)


def test_the_approved_with_override_customer_workflow_succeeds(
    service, approval_service, people, email_service
):
    task = submit_quotation(
        service,
        approval_service,
        people,
        "Q7-CU-3",
        status="review_required",
        margin="18.0",
    )
    _approve(approval_service, people, task, override=True)
    draft = email_service.draft_customer_email(
        "Q7-CU-3", user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
    )
    record = email_service.send_reviewed_customer_email(
        draft, user=people["sales"], draft_approved=True
    )
    assert record.status == EmailStatus.SENT.value


def test_the_customer_email_never_exposes_internal_commercial_detail(
    service, approval_service, people, email_service, email_provider
):
    task = submit_quotation(
        service,
        approval_service,
        people,
        "Q7-CU-4",
        status="review_required",
        margin="18.0",
    )
    _approve(approval_service, people, task, override=True)
    draft = email_service.draft_customer_email(
        "Q7-CU-4", user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
    )
    email_service.send_reviewed_customer_email(
        draft, user=people["sales"], draft_approved=True
    )
    _, message = email_provider.sent[0]
    text = f"{message.subject}\n{message.body}".casefold()
    for forbidden in ("margin", "cost", "threshold", "override", "rule"):
        assert forbidden not in text
    assert "18.0" not in text


def test_the_pdf_attachment_matches_the_approved_quotation_version(
    service, approval_service, people, email_service, session_factory
):
    task = submit_quotation(service, approval_service, people, "Q7-CU-5")
    _approve(approval_service, people, task)
    loaded = service.load_quotation("Q7-CU-5")

    draft = email_service.draft_customer_email(
        "Q7-CU-5", user=people["sales"], recipients=(CUSTOMER_ADDRESS,)
    )
    attachment = draft.message.attachments[0]
    assert attachment.quotation_version == loaded.version
    assert attachment.mime_type == "application/pdf"
    assert attachment.content.startswith(b"%PDF")
    assert draft.record.attachment_document_ids == (attachment.document_id,)


# -- owner notifications ------------------------------------------------


def test_a_revision_request_goes_to_the_quotation_owner(
    service, approval_service, people, email_service, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-OW-1")
    approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="request_revision",
        reason="Please add the extended warranty line.",
    )
    record = email_service.send_owner_notification(
        "Q7-OW-1",
        email_type=EmailType.REVISION_REQUEST,
        user=people["manager"],
        reason="Please add the extended warranty line.",
    )
    assert record.recipients == ("sam.sales@internal.invalid",)
    _, message = email_provider.sent[0]
    assert "Please add the extended warranty line." in message.body


def test_a_rejection_notification_is_internal_only(
    service, approval_service, people, email_service, email_provider
):
    task = submit_quotation(
        service,
        approval_service,
        people,
        "Q7-OW-2",
        status="review_required",
        margin="12.0",
    )
    approval_service.act(
        user=people["manager"],
        task_id=task.id,
        action="reject",
        reason="Margin is not acceptable for this configuration.",
    )
    record = email_service.send_owner_notification(
        "Q7-OW-2",
        email_type=EmailType.REJECTION_NOTIFICATION,
        user=people["manager"],
        reason="Margin is not acceptable for this configuration.",
    )
    assert record.audience == "internal"
    _, message = email_provider.sent[0]
    assert "must not be forwarded to the customer" in message.body


def test_an_unauthenticated_caller_cannot_send(email_service):
    with pytest.raises(PermissionDeniedError):
        email_service.compose_approval_request(1, user=None)


# -- persistence --------------------------------------------------------


def test_the_body_is_not_persisted_in_full_by_default(
    service, approval_service, people, email_service
):
    task = submit_quotation(service, approval_service, people, "Q7-PS-1")
    record = email_service.send_approval_request(task.id, user=people["sales"])
    assert record.body == ""
    assert len(record.body_hash) == 64
    assert record.template_version == "v1"
    assert record.agent_provider == "deterministic"


def test_redacted_body_storage_keeps_only_the_shape(
    service, approval_service, people, session_factory, email_provider
):
    task = submit_quotation(service, approval_service, people, "Q7-PS-2")
    email_service = EmailService(
        session_factory,
        config=email_config(body_storage="redacted"),
        provider=email_provider,
    )
    record = email_service.send_approval_request(task.id, user=people["sales"])
    assert record.body.startswith("[redacted body:")
    assert "Q7-PS-2" not in record.body


def test_the_idempotency_key_follows_the_documented_shape():
    assert build_idempotency_key(
        quotation_id="Q7-1",
        quotation_version=3,
        approval_task_id=7,
        email_type="approval_reminder",
        reminder_cycle=1,
    ) == "Q7-1|3|7|approval_reminder|1"


def test_a_provider_failure_is_persisted_against_the_email_record(
    service, approval_service, people, session_factory
):
    from tests.fixtures.phase7_helpers import FailingProvider
    from app.emailing.contracts import DeliveryErrorCategory
    from app.services.unit_of_work import UnitOfWork

    task = submit_quotation(service, approval_service, people, "Q7-PS-3")
    provider = FailingProvider(failures=1)
    failing_service = EmailService(
        session_factory, config=email_config(), provider=provider
    )
    record = failing_service.send_approval_request(task.id, user=people["sales"])
    assert record.status == EmailStatus.FAILED.value
    assert record.last_error_category == DeliveryErrorCategory.TRANSIENT.value
    assert record.attempt_count == 1

    retried = failing_service.send_approval_request(task.id, user=people["sales"])
    assert retried.status == EmailStatus.SENT.value
    assert retried.id == record.id

    with UnitOfWork(session_factory) as uow:
        stored = uow.emails.list_for_quotation("Q7-PS-3")
    assert len(stored) == 1


def test_a_failed_internal_email_can_be_retried_by_an_authorised_user(
    service, approval_service, people, session_factory
):
    from tests.fixtures.phase7_helpers import FailingProvider

    task = submit_quotation(service, approval_service, people, "Q7-RT-1")
    provider = FailingProvider(failures=1)
    emails = EmailService(
        session_factory, config=email_config(), provider=provider
    )
    failed = emails.send_approval_request(task.id, user=people["sales"])
    assert failed.status == EmailStatus.FAILED.value

    retried = emails.retry_delivery(failed.id, user=people["sales"])
    assert retried.status == EmailStatus.SENT.value
    assert retried.id == failed.id


def test_a_sent_email_cannot_be_retried(
    service, approval_service, people, email_service
):
    task = submit_quotation(service, approval_service, people, "Q7-RT-2")
    sent = email_service.send_approval_request(task.id, user=people["sales"])
    with pytest.raises(EmailNotAllowedError):
        email_service.retry_delivery(sent.id, user=people["sales"])


def test_a_permanently_failed_email_is_not_retried(
    service, approval_service, people, session_factory
):
    from app.emailing.contracts import DeliveryErrorCategory
    from tests.fixtures.phase7_helpers import FailingProvider

    task = submit_quotation(service, approval_service, people, "Q7-RT-3")
    provider = FailingProvider(
        failures=3, category=DeliveryErrorCategory.PERMANENT
    )
    emails = EmailService(
        session_factory, config=email_config(), provider=provider
    )
    failed = emails.send_approval_request(task.id, user=people["sales"])
    with pytest.raises(EmailNotAllowedError):
        emails.retry_delivery(failed.id, user=people["sales"])
    assert provider.attempts == 1
