"""Email composition, delivery and reminder scheduling (Phase 7)."""

from __future__ import annotations

from app.emailing.composition import (
    ComposedEmail,
    EmailFacts,
    EmailLineItem,
    audience_for,
    build_email_facts,
    compose_email,
    customer_boundary_problems,
    require_customer_approval,
)
from app.emailing.config import (
    ENVIRONMENT_VARIABLES,
    EmailConfig,
    EmailConfigurationError,
    GraphSettings,
    SMTPSettings,
    load_email_config,
)
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
    PermanentDeliveryError,
    RecipientValidationError,
    TransientDeliveryError,
)
from app.emailing.providers import (
    ConsoleEmailProvider,
    GraphTransport,
    MicrosoftGraphEmailProvider,
    SMTPEmailProvider,
    build_delivery_provider,
)
from app.emailing.recipients import (
    is_internal_address,
    is_valid_address,
    resolve_user_address,
    validate_recipients,
)
from app.emailing.reminders import ApprovalReminderWorker, ReminderRunReport
from app.emailing.service import (
    EmailDraft,
    EmailService,
    build_idempotency_key,
    reminder_due_at,
)

__all__ = [
    "ApprovalReminderWorker",
    "ComposedEmail",
    "ConsoleEmailProvider",
    "DeliveryErrorCategory",
    "ENVIRONMENT_VARIABLES",
    "EmailAttachment",
    "EmailAudience",
    "EmailConfig",
    "EmailConfigurationError",
    "EmailDeliveryError",
    "EmailDeliveryProvider",
    "EmailDeliveryResult",
    "EmailDraft",
    "EmailError",
    "EmailFacts",
    "EmailLineItem",
    "EmailNotAllowedError",
    "EmailService",
    "EmailStatus",
    "EmailType",
    "GraphSettings",
    "GraphTransport",
    "MicrosoftGraphEmailProvider",
    "OutboundEmail",
    "PermanentDeliveryError",
    "RecipientValidationError",
    "ReminderRunReport",
    "SMTPEmailProvider",
    "SMTPSettings",
    "TransientDeliveryError",
    "audience_for",
    "build_delivery_provider",
    "build_email_facts",
    "build_idempotency_key",
    "compose_email",
    "customer_boundary_problems",
    "is_internal_address",
    "is_valid_address",
    "load_email_config",
    "reminder_due_at",
    "require_customer_approval",
    "resolve_user_address",
    "validate_recipients",
]
