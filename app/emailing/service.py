"""Email use cases: composition, delivery, persistence and reminders.

Responsibilities are deliberately separated:

* :mod:`app.emailing.composition` renders content from persisted facts;
* :mod:`app.emailing.recipients` resolves and validates addresses;
* :mod:`app.emailing.providers` delivers;
* this module persists every draft, attempt and outcome, and gates the
  customer email behind an approval decision and a human draft review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any, Mapping

from sqlalchemy.orm import Session, sessionmaker

from app.agents.agents import Agent3EmailWordingAgent
from app.auth.provider import AuthenticatedUser, PermissionDeniedError
from app.auth.roles import Permission
from app.domain.dto import ApprovalTaskDTO, EmailRecordDTO
from app.emailing.composition import (
    ComposedEmail,
    EmailFacts,
    audience_for,
    build_email_facts,
    compose_email,
    require_customer_approval,
)
from app.emailing.config import EmailConfig, load_email_config
from app.emailing.contracts import (
    DeliveryErrorCategory,
    EmailAttachment,
    EmailAudience,
    EmailDeliveryError,
    EmailDeliveryProvider,
    EmailDeliveryResult,
    EmailError,
    EmailNotAllowedError,
    EmailStatus,
    EmailType,
    OutboundEmail,
    PERMANENT_ERROR_CATEGORIES,
    RecipientValidationError,
)
from app.emailing.providers import build_delivery_provider
from app.emailing.recipients import resolve_user_address, validate_recipients
from app.quotation_models import utc_now
from app.services.unit_of_work import UnitOfWork

TASK_STATUS_PENDING = "pending_review"

REMINDER_TYPE = "two_day_pending_approval"


def build_idempotency_key(
    *,
    quotation_id: str,
    quotation_version: int,
    approval_task_id: int | None,
    email_type: str,
    reminder_cycle: int = 0,
) -> str:
    """The suggested key: quotation, version, task, type and cycle."""

    return "|".join(
        [
            quotation_id,
            str(quotation_version),
            str(approval_task_id or 0),
            email_type,
            str(reminder_cycle),
        ]
    )


def body_hash(body: str) -> str:
    return sha256(body.encode("utf-8")).hexdigest()


def redact_body(body: str) -> str:
    """Keep the shape of a message without its content."""

    lines = body.splitlines()
    return f"[redacted body: {len(lines)} line(s), {len(body)} character(s)]"


def storage_body(body: str, config: EmailConfig) -> str:
    if config.body_storage == "full":
        return body
    if config.body_storage == "redacted":
        return redact_body(body)
    return ""


@dataclass(frozen=True)
class EmailDraft:
    """A composed but not yet delivered email, plus its persisted record."""

    record: EmailRecordDTO
    message: OutboundEmail
    composed: ComposedEmail


class EmailService:
    """Authenticated email composition and delivery use cases."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        *,
        config: EmailConfig | None = None,
        provider: EmailDeliveryProvider | None = None,
        agent: Agent3EmailWordingAgent | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.config = config or load_email_config(environment)
        self.provider = provider or build_delivery_provider(
            self.config, environment=environment
        )
        self.agent = agent

    def _unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory)

    # -- shared helpers ------------------------------------------------

    @staticmethod
    def _require(user: AuthenticatedUser | None, permission: Permission) -> None:
        if user is None:
            raise PermissionDeniedError(
                "An authenticated user is required for this action."
            )
        user.require(permission)

    def _load_context(self, uow: UnitOfWork, quotation_id: str):
        from app.domain.workflow_state_codec import load_workflow_state

        record = uow.quotations.get_by_quotation_id(quotation_id)
        if record is None:
            raise EmailError(f"Unknown quotation: {quotation_id}")
        return record, load_workflow_state(record.state_document)

    def _persist_draft(
        self,
        uow: UnitOfWork,
        *,
        quotation_id: str,
        message: OutboundEmail,
        composed: ComposedEmail,
        status: EmailStatus,
        idempotency_key: str,
        reminder_cycle: int = 0,
        created_by_user_id: int | None = None,
    ) -> EmailRecordDTO:
        return uow.emails.create(
            quotation_id=quotation_id,
            email_type=message.email_type.value,
            audience=message.audience.value,
            sender=message.sender,
            recipients=message.recipients,
            cc_recipients=message.cc,
            bcc_recipients=message.bcc,
            subject=message.subject,
            body=storage_body(message.body, self.config),
            body_hash=body_hash(message.body),
            body_storage_mode=self.config.body_storage,
            quotation_version=message.quotation_version,
            approval_task_id=message.approval_task_id,
            template_version=composed.template_version,
            agent_provider=composed.agent_provider,
            agent_fallback_used=composed.fallback_used,
            agent_fallback_reason=composed.fallback_reason,
            delivery_provider=self.provider.provider_name,
            status=status.value,
            idempotency_key=idempotency_key,
            attachment_document_ids=message.attachment_document_ids,
            reminder_cycle=reminder_cycle,
            created_by_user_id=created_by_user_id,
        )

    def _deliver(
        self,
        *,
        email_record_id: int,
        message: OutboundEmail,
        idempotency_key: str,
    ) -> EmailRecordDTO:
        """Attempt delivery once and persist the outcome either way."""

        with self._unit_of_work() as uow:
            existing = uow.emails.get(email_record_id)
            if existing is None:
                raise EmailError("The email record no longer exists.")
            if existing.status == EmailStatus.SENT.value:
                return existing
            if existing.attempt_count >= self.config.max_delivery_attempts:
                raise EmailNotAllowedError(
                    "The configured delivery attempt limit has been reached "
                    "for this email."
                )
            if (
                existing.last_error_category
                in {category.value for category in PERMANENT_ERROR_CATEGORIES}
                and existing.attempt_count > 0
            ):
                raise EmailNotAllowedError(
                    "This email failed permanently and must be corrected "
                    "before another attempt: "
                    f"{existing.last_error_category}."
                )
            uow.emails.record_attempt(
                email_record_id=email_record_id,
                status=EmailStatus.QUEUED.value,
                increment_attempt=False,
                error_category=existing.last_error_category,
                error_detail=existing.last_error_detail,
            )
            uow.commit()

        try:
            result = self.provider.send(
                message=message, idempotency_key=idempotency_key
            )
        except EmailDeliveryError as error:
            return self._record_failure(
                email_record_id, error.category, str(error)
            )
        except Exception as error:  # noqa: BLE001 - never lose the record
            return self._record_failure(
                email_record_id,
                DeliveryErrorCategory.TRANSIENT,
                type(error).__name__,
            )
        return self._record_success(email_record_id, result)

    def _record_success(
        self, email_record_id: int, result: EmailDeliveryResult
    ) -> EmailRecordDTO:
        with self._unit_of_work() as uow:
            updated = uow.emails.record_attempt(
                email_record_id=email_record_id,
                status=EmailStatus.SENT.value,
                moment=result.occurred_at,
                error_category=DeliveryErrorCategory.NONE.value,
                provider_message_id=result.provider_message_id,
            )
            uow.commit()
        assert updated is not None
        return updated

    def _record_failure(
        self,
        email_record_id: int,
        category: DeliveryErrorCategory,
        detail: str,
    ) -> EmailRecordDTO:
        with self._unit_of_work() as uow:
            updated = uow.emails.record_attempt(
                email_record_id=email_record_id,
                status=EmailStatus.FAILED.value,
                error_category=category.value,
                error_detail=detail,
            )
            uow.commit()
        assert updated is not None
        return updated

    # -- approval request ----------------------------------------------

    def compose_approval_request(
        self,
        task_id: int,
        *,
        user: AuthenticatedUser,
    ) -> EmailDraft:
        """Compose the approval-request email for a pending task."""

        self._require(user, Permission.SUBMIT_QUOTATION)
        with self._unit_of_work() as uow:
            task = uow.approvals.get_task(task_id)
            if task is None:
                raise EmailError(f"Unknown approval task: {task_id}")
            if task.status != TASK_STATUS_PENDING:
                raise EmailNotAllowedError(
                    "An approval-request email requires a pending task."
                )
            approver = (
                None
                if task.assigned_user_id is None
                else uow.users.get(task.assigned_user_id)
            )
            # The approver address always comes from the stored user record.
            address = resolve_user_address(
                approver, config=self.config, role_label="assigned approver"
            )
            record, state = self._load_context(uow, task.quotation_reference)
            facts = build_email_facts(
                quotation=record,
                state=state,
                task=task,
                approver_name=(
                    approver.display_name or approver.username
                    if approver is not None
                    else task.assigned_approver_name
                ),
            )
        include_margin = self._may_see_margin(approver)
        composed = compose_email(
            email_type=EmailType.APPROVAL_REQUEST,
            audience=EmailAudience.INTERNAL,
            facts=facts,
            include_margin=include_margin,
            agent=self.agent,
            template_version=self.config.template_version,
        )
        message = self._build_message(
            composed, facts, recipients=(address,), task=task
        )
        key = build_idempotency_key(
            quotation_id=facts.quotation_id,
            quotation_version=facts.quotation_version,
            approval_task_id=task.id,
            email_type=EmailType.APPROVAL_REQUEST.value,
        )
        with self._unit_of_work() as uow:
            existing = uow.emails.get_by_idempotency_key(key)
            if existing is not None:
                return EmailDraft(existing, message, composed)
            created = self._persist_draft(
                uow,
                quotation_id=facts.quotation_id,
                message=message,
                composed=composed,
                status=EmailStatus.DRAFTED,
                idempotency_key=key,
                created_by_user_id=user.user_id,
            )
            uow.commit()
        return EmailDraft(created, message, composed)

    def send_approval_request(
        self,
        task_id: int,
        *,
        user: AuthenticatedUser,
    ) -> EmailRecordDTO:
        draft = self.compose_approval_request(task_id, user=user)
        if draft.record.status == EmailStatus.SENT.value:
            return draft.record
        return self._deliver(
            email_record_id=draft.record.id,
            message=draft.message,
            idempotency_key=draft.record.idempotency_key,
        )

    def send_approval_request_on_submission(
        self,
        task_id: int,
        *,
        user: AuthenticatedUser,
    ) -> EmailRecordDTO | None:
        """Send automatically only when configuration explicitly enables it."""

        if not self.config.auto_send_approval_request:
            return None
        return self.send_approval_request(task_id, user=user)

    @staticmethod
    def _may_see_margin(user_dto) -> bool:
        """Only a recipient with commercial-detail permission sees margin."""

        from app.auth.roles import parse_role, permissions_for

        if user_dto is None:
            return False
        try:
            roles = tuple(parse_role(role) for role in user_dto.roles)
        except Exception:  # noqa: BLE001 - unknown role means no privilege
            return False
        return Permission.VIEW_COMMERCIAL_DETAIL in permissions_for(roles)

    def _build_message(
        self,
        composed: ComposedEmail,
        facts: EmailFacts,
        *,
        recipients: tuple[str, ...],
        task: ApprovalTaskDTO | None = None,
        attachments: tuple[EmailAttachment, ...] = (),
        cc: tuple[str, ...] = (),
    ) -> OutboundEmail:
        validated = validate_recipients(
            recipients,
            email_type=composed.email_type,
            audience=composed.audience,
            config=self.config,
        )
        validated_cc = (
            validate_recipients(
                cc,
                email_type=composed.email_type,
                audience=composed.audience,
                config=self.config,
                field_name="cc_recipients",
            )
            if cc
            else ()
        )
        return OutboundEmail(
            email_type=composed.email_type,
            audience=composed.audience,
            sender=self.config.sender_address,
            recipients=validated,
            cc=validated_cc,
            subject=composed.subject,
            body=composed.body,
            quotation_id=facts.quotation_id,
            quotation_version=facts.quotation_version,
            approval_task_id=None if task is None else task.id,
            template_version=composed.template_version,
            attachments=attachments,
        )

    # -- reminder ------------------------------------------------------

    def send_reminder(
        self,
        task: ApprovalTaskDTO,
        *,
        reminder_cycle: int,
    ) -> EmailRecordDTO:
        """Compose and deliver one reminder for a claimed task.

        Called by the reminder worker only, after the task status has been
        rechecked inside the claiming transaction.
        """

        with self._unit_of_work() as uow:
            current = uow.approvals.get_task(task.id)
            if current is None or current.status != TASK_STATUS_PENDING:
                raise EmailNotAllowedError(
                    "The approval task is no longer pending; no reminder is "
                    "sent."
                )
            approver = (
                None
                if current.assigned_user_id is None
                else uow.users.get(current.assigned_user_id)
            )
            address = resolve_user_address(
                approver, config=self.config, role_label="assigned approver"
            )
            record, state = self._load_context(uow, current.quotation_reference)
            facts = build_email_facts(
                quotation=record,
                state=state,
                task=current,
                approver_name=(
                    approver.display_name or approver.username
                    if approver is not None
                    else current.assigned_approver_name
                ),
            )
            # The reminder restates the decision recorded at submission time.
            facts = EmailFacts(
                **{
                    **facts.__dict__,
                    "decision_status": current.decision_status
                    or facts.decision_status,
                }
            )
        composed = compose_email(
            email_type=EmailType.APPROVAL_REMINDER,
            audience=EmailAudience.INTERNAL,
            facts=facts,
            include_margin=self._may_see_margin(approver),
            agent=self.agent,
            template_version=self.config.template_version,
        )
        message = self._build_message(
            composed, facts, recipients=(address,), task=current
        )
        key = build_idempotency_key(
            quotation_id=facts.quotation_id,
            quotation_version=facts.quotation_version,
            approval_task_id=current.id,
            email_type=REMINDER_TYPE,
            reminder_cycle=reminder_cycle,
        )
        with self._unit_of_work() as uow:
            existing = uow.emails.get_by_idempotency_key(key)
            if existing is not None and existing.status == EmailStatus.SENT.value:
                return existing
            if existing is not None:
                created = existing
            else:
                created = self._persist_draft(
                    uow,
                    quotation_id=facts.quotation_id,
                    message=message,
                    composed=composed,
                    status=EmailStatus.DRAFTED,
                    idempotency_key=key,
                    reminder_cycle=reminder_cycle,
                )
            uow.commit()
        return self._deliver(
            email_record_id=created.id,
            message=message,
            idempotency_key=key,
        )

    # -- customer email ------------------------------------------------

    def draft_customer_email(
        self,
        quotation_id: str,
        *,
        user: AuthenticatedUser,
        recipients: tuple[str, ...],
        attach_pdf: bool = True,
    ) -> EmailDraft:
        """Compose the customer email and hold it for human draft review.

        The draft is persisted as ``pending_review``. Nothing is delivered
        until :meth:`send_reviewed_customer_email` is called.
        """

        self._require(user, Permission.SUBMIT_QUOTATION)
        attachments: tuple[EmailAttachment, ...] = ()
        with self._unit_of_work() as uow:
            record, state = self._load_context(uow, quotation_id)
            facts = build_email_facts(quotation=record, state=state)
            require_customer_approval(facts)
            if attach_pdf:
                attachments = self._resolve_pdf_attachment(
                    uow,
                    quotation_id=quotation_id,
                    quotation_version=record.version,
                    state=state,
                    user=user,
                )
                uow.commit()
        composed = compose_email(
            email_type=EmailType.CUSTOMER_QUOTATION,
            audience=EmailAudience.CUSTOMER,
            facts=facts,
            agent=self.agent,
            template_version=self.config.template_version,
        )
        message = self._build_message(
            composed, facts, recipients=recipients, attachments=attachments
        )
        key = build_idempotency_key(
            quotation_id=facts.quotation_id,
            quotation_version=facts.quotation_version,
            approval_task_id=None,
            email_type=EmailType.CUSTOMER_QUOTATION.value,
        )
        with self._unit_of_work() as uow:
            existing = uow.emails.get_by_idempotency_key(key)
            if existing is not None:
                return EmailDraft(existing, message, composed)
            created = self._persist_draft(
                uow,
                quotation_id=facts.quotation_id,
                message=message,
                composed=composed,
                status=EmailStatus.PENDING_REVIEW,
                idempotency_key=key,
                created_by_user_id=user.user_id,
            )
            uow.commit()
        return EmailDraft(created, message, composed)

    def send_reviewed_customer_email(
        self,
        draft: EmailDraft,
        *,
        user: AuthenticatedUser,
        draft_approved: bool,
    ) -> EmailRecordDTO:
        """Deliver a customer email only after an explicit human review."""

        self._require(user, Permission.SUBMIT_QUOTATION)
        if not draft_approved:
            raise EmailNotAllowedError(
                "The customer email draft must be reviewed and approved by a "
                "person before it can be sent."
            )
        with self._unit_of_work() as uow:
            record, state = self._load_context(uow, draft.message.quotation_id)
            facts = build_email_facts(quotation=record, state=state)
            require_customer_approval(facts)
            if record.version != draft.message.quotation_version:
                raise EmailNotAllowedError(
                    "The quotation changed after this draft was composed. "
                    "Compose the customer email again."
                )
        return self._deliver(
            email_record_id=draft.record.id,
            message=draft.message,
            idempotency_key=draft.record.idempotency_key,
        )

    def _resolve_pdf_attachment(
        self,
        uow: UnitOfWork,
        *,
        quotation_id: str,
        quotation_version: int,
        state,
        user: AuthenticatedUser,
    ) -> tuple[EmailAttachment, ...]:
        """Return the approved PDF for the current quotation version."""

        from app.documents.context import build_customer_document_context
        from app.documents.plan import deterministic_document_plan
        from app.documents.renderer import render_quotation_pdf

        document = uow.documents.latest_for_version(
            quotation_id=quotation_id,
            quotation_version=quotation_version,
            kind="customer_pdf",
        )
        if document is None:
            context = build_customer_document_context(
                state, quotation_version=quotation_version
            )
            rendered = render_quotation_pdf(
                context, deterministic_document_plan()
            )
            document_id = uow.documents.add(
                quotation_id=quotation_id,
                kind="customer_pdf",
                audience="customer",
                filename=rendered.filename,
                mime_type=rendered.mime_type,
                content=rendered.content,
                quotation_version=quotation_version,
                generated_by_user_id=user.user_id,
                template_version=rendered.template_version,
                document_plan_version=rendered.plan_version,
                agent_provider="deterministic",
                render_engine=rendered.engine,
            )
            document = uow.documents.get(document_id)
        assert document is not None
        return (
            EmailAttachment(
                document_id=document.id,
                filename=document.filename,
                mime_type=document.mime_type,
                content=document.content,
                quotation_version=document.quotation_version,
            ),
        )

    # -- owner notifications -------------------------------------------

    def send_owner_notification(
        self,
        quotation_id: str,
        *,
        email_type: EmailType,
        user: AuthenticatedUser,
        reason: str = "",
    ) -> EmailRecordDTO:
        """Send a revision-request or rejection notification to the owner."""

        if email_type not in {
            EmailType.REVISION_REQUEST,
            EmailType.REJECTION_NOTIFICATION,
        }:
            raise EmailError(f"{email_type.value} is not an owner notification.")
        self._require(user, Permission.VIEW_APPROVAL_TASKS)
        with self._unit_of_work() as uow:
            record, state = self._load_context(uow, quotation_id)
            owner = (
                None
                if record.owner_user_id is None
                else uow.users.get(record.owner_user_id)
            )
            address = resolve_user_address(
                owner, config=self.config, role_label="quotation owner"
            )
            task = uow.approvals.list_tasks(quotation_id=quotation_id)
            latest = task[-1] if task else None
            facts = build_email_facts(
                quotation=record,
                state=state,
                task=latest,
                approver_name=user.display_name or user.username,
                reason=reason,
            )
        include_rules = user.has_permission(Permission.VIEW_COMMERCIAL_DETAIL)
        composed = compose_email(
            email_type=email_type,
            audience=EmailAudience.INTERNAL,
            facts=facts,
            include_rules=include_rules,
            agent=self.agent,
            template_version=self.config.template_version,
        )
        message = self._build_message(
            composed, facts, recipients=(address,), task=latest
        )
        key = build_idempotency_key(
            quotation_id=facts.quotation_id,
            quotation_version=facts.quotation_version,
            approval_task_id=None if latest is None else latest.id,
            email_type=email_type.value,
        )
        with self._unit_of_work() as uow:
            existing = uow.emails.get_by_idempotency_key(key)
            if existing is not None and existing.status == EmailStatus.SENT.value:
                return existing
            created = existing or self._persist_draft(
                uow,
                quotation_id=facts.quotation_id,
                message=message,
                composed=composed,
                status=EmailStatus.DRAFTED,
                idempotency_key=key,
                created_by_user_id=user.user_id,
            )
            uow.commit()
        return self._deliver(
            email_record_id=created.id,
            message=message,
            idempotency_key=key,
        )

    # -- operations ----------------------------------------------------

    def retry_delivery(
        self, email_record_id: int, *, user: AuthenticatedUser
    ) -> EmailRecordDTO:
        """Recompose and resend a failed email under its original key.

        Retrying never reuses a stored body: the deterministic templates are
        rendered again from the persisted domain state, so a retry can never
        deliver stale or tampered content. Only failed internal emails may be
        retried this way; a customer email must go through the human draft
        review step again.
        """

        self._require(user, Permission.SUBMIT_QUOTATION)
        with self._unit_of_work() as uow:
            record = uow.emails.get(email_record_id)
        if record is None:
            raise EmailError(f"Unknown email record: {email_record_id}")
        if record.status != EmailStatus.FAILED.value:
            raise EmailNotAllowedError("Only a failed email can be retried.")
        if record.last_error_category in {
            category.value for category in PERMANENT_ERROR_CATEGORIES
        }:
            raise EmailNotAllowedError(
                "This email failed permanently ("
                f"{record.last_error_category}) and must be corrected in "
                "configuration or in the user record before another attempt."
            )
        if record.email_type == EmailType.APPROVAL_REQUEST.value:
            if record.approval_task_id is None:
                raise EmailError("The approval task reference is missing.")
            return self.send_approval_request(
                record.approval_task_id, user=user
            )
        if record.email_type in {
            EmailType.REVISION_REQUEST.value,
            EmailType.REJECTION_NOTIFICATION.value,
        }:
            return self.send_owner_notification(
                record.quotation_reference,
                email_type=EmailType(record.email_type),
                user=user,
            )
        raise EmailNotAllowedError(
            f"{record.email_type} emails are not retried from the UI."
        )

    def list_emails(
        self, quotation_id: str, *, user: AuthenticatedUser
    ) -> tuple[EmailRecordDTO, ...]:
        self._require(user, Permission.VIEW_OWN_QUOTATIONS)
        with self._unit_of_work() as uow:
            return uow.emails.list_for_quotation(quotation_id)

    def describe_configuration(self) -> dict[str, Any]:
        """Secret-free configuration snapshot for the UI."""

        return self.config.describe()


def reminder_due_at(
    submitted_at: datetime, config: EmailConfig | None = None
) -> datetime:
    """Submission time plus the configured reminder delay (default two days)."""

    resolved = config or load_email_config()
    return submitted_at + timedelta(hours=resolved.reminder_delay_hours)


__all__ = [
    "EmailDraft",
    "EmailService",
    "REMINDER_TYPE",
    "build_idempotency_key",
    "body_hash",
    "redact_body",
    "reminder_due_at",
    "storage_body",
    "RecipientValidationError",
]
