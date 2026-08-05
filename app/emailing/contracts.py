"""Email contracts shared by composition, delivery and persistence.

Nothing in this module knows how an email is sent. Delivery adapters
implement :class:`EmailDeliveryProvider`; composition produces
:class:`OutboundEmail` values from persisted domain state only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from app.quotation_models import utc_now


class EmailType(str, Enum):
    """The closed set of emails the workflow may produce."""

    APPROVAL_REQUEST = "approval_request"
    APPROVAL_REMINDER = "approval_reminder"
    CUSTOMER_QUOTATION = "customer_quotation"
    REVISION_REQUEST = "revision_request"
    REJECTION_NOTIFICATION = "rejection_notification"


#: Emails that may leave the company. Everything else is internal only.
CUSTOMER_FACING_TYPES = frozenset({EmailType.CUSTOMER_QUOTATION})


class EmailAudience(str, Enum):
    INTERNAL = "internal"
    CUSTOMER = "customer"


class EmailStatus(str, Enum):
    """Lifecycle of one persisted email record."""

    DRAFTED = "drafted"
    PENDING_REVIEW = "pending_review"
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeliveryErrorCategory(str, Enum):
    """Normalised delivery failure reasons.

    ``TRANSIENT`` failures may be retried; every other category is permanent
    and must not be retried indefinitely.
    """

    NONE = "none"
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    RECIPIENT = "recipient"
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"


#: Categories that must never be retried automatically.
PERMANENT_ERROR_CATEGORIES = frozenset(
    {
        DeliveryErrorCategory.PERMANENT,
        DeliveryErrorCategory.RECIPIENT,
        DeliveryErrorCategory.CONFIGURATION,
        DeliveryErrorCategory.AUTHENTICATION,
    }
)


class EmailError(RuntimeError):
    """Base class for email workflow failures."""


class EmailDeliveryError(EmailError):
    """Raised by a delivery provider. Always carries a category."""

    category = DeliveryErrorCategory.PERMANENT

    def __init__(self, message: str, *, category: DeliveryErrorCategory | None = None):
        super().__init__(message)
        if category is not None:
            self.category = category


class TransientDeliveryError(EmailDeliveryError):
    category = DeliveryErrorCategory.TRANSIENT


class PermanentDeliveryError(EmailDeliveryError):
    category = DeliveryErrorCategory.PERMANENT


class RecipientValidationError(EmailError):
    """Raised when a recipient list is empty, malformed or not permitted."""


class EmailNotAllowedError(EmailError):
    """Raised when policy forbids composing or sending this email now."""


@dataclass(frozen=True)
class EmailAttachment:
    """A binary attachment resolved from a persisted generated document."""

    document_id: int
    filename: str
    mime_type: str
    content: bytes
    quotation_version: int = 0


@dataclass(frozen=True)
class OutboundEmail:
    """An email ready to be handed to a delivery provider."""

    email_type: EmailType
    audience: EmailAudience
    sender: str
    recipients: tuple[str, ...]
    subject: str
    body: str
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    quotation_id: str = ""
    quotation_version: int = 0
    approval_task_id: int | None = None
    template_version: str = "v1"
    attachments: tuple[EmailAttachment, ...] = ()

    def __post_init__(self) -> None:
        if not self.recipients:
            raise RecipientValidationError(
                "An outbound email requires at least one recipient."
            )
        if not self.subject.strip():
            raise EmailError("An outbound email requires a subject.")
        if not self.body.strip():
            raise EmailError("An outbound email requires a body.")

    @property
    def attachment_document_ids(self) -> tuple[int, ...]:
        return tuple(item.document_id for item in self.attachments)


@dataclass(frozen=True)
class EmailDeliveryResult:
    """The outcome of one delivery attempt."""

    delivered: bool
    provider: str
    provider_message_id: str = ""
    error_category: DeliveryErrorCategory = DeliveryErrorCategory.NONE
    error_detail: str = ""
    idempotency_key: str = ""
    occurred_at: datetime = field(default_factory=utc_now)
    deduplicated: bool = False


@runtime_checkable
class EmailDeliveryProvider(Protocol):
    """The delivery interface every adapter implements."""

    provider_name: str

    def send(
        self,
        *,
        message: OutboundEmail,
        idempotency_key: str,
    ) -> EmailDeliveryResult: ...


@dataclass(frozen=True)
class ProviderHealth:
    """Secret-free readiness snapshot for a delivery provider."""

    provider_name: str
    configured: bool
    detail: str
