"""Phase 7 helpers: email configuration, providers and submitted quotations."""

from __future__ import annotations

from app.emailing.config import EmailConfig, GraphSettings, SMTPSettings
from app.emailing.contracts import (
    DeliveryErrorCategory,
    EmailDeliveryResult,
    OutboundEmail,
    PermanentDeliveryError,
    TransientDeliveryError,
)

INTERNAL_DOMAIN = "internal.invalid"
CUSTOMER_ADDRESS = "buyer@customer.example"


def email_config(**overrides) -> EmailConfig:
    """A deterministic console configuration with no secret value anywhere."""

    defaults = dict(
        provider="console",
        sender_address=f"quotation-bot@{INTERNAL_DOMAIN}",
        internal_domains=(INTERNAL_DOMAIN,),
        allow_customer_delivery=True,
        auto_send_approval_request=False,
        body_storage="hash",
        max_delivery_attempts=3,
        template_version="v1",
        reminder_delay_hours=48.0,
        reminder_max_count=1,
        smtp=SMTPSettings(),
        graph=GraphSettings(),
    )
    defaults.update(overrides)
    return EmailConfig(**defaults)


class FailingProvider:
    """Provider that fails a configurable number of times, then succeeds."""

    provider_name = "failing"

    def __init__(
        self,
        *,
        failures: int = 1,
        category: DeliveryErrorCategory = DeliveryErrorCategory.TRANSIENT,
    ) -> None:
        self.failures = failures
        self.category = category
        self.attempts = 0
        self.sent: list[OutboundEmail] = []

    def send(
        self, *, message: OutboundEmail, idempotency_key: str
    ) -> EmailDeliveryResult:
        self.attempts += 1
        if self.attempts <= self.failures:
            if self.category is DeliveryErrorCategory.TRANSIENT:
                raise TransientDeliveryError("Simulated transient failure.")
            raise PermanentDeliveryError(
                "Simulated permanent failure.", category=self.category
            )
        self.sent.append(message)
        return EmailDeliveryResult(
            delivered=True,
            provider=self.provider_name,
            provider_message_id=idempotency_key,
            idempotency_key=idempotency_key,
        )


def add_line_items(state) -> None:
    """Attach deterministic line items to a workflow state draft."""

    from app.quotation_models import LineItemCategory, QuotationLineItem

    state.draft.customer_name = "Northwind Medical"
    state.draft.currency = "USD"
    state.draft.incoterm = "DAP"
    state.draft.product_query = "Synthetic imaging system"
    state.draft.selected_product_ids = ["SYN-MAIN-1"]
    state.draft.line_items = [
        QuotationLineItem(
            line_id="L1",
            product_id="SYN-MAIN-1",
            description="Synthetic imaging system",
            category=LineItemCategory.MAIN_PRODUCT,
            quantity=1,
            unit_price=100000.0,
            currency="USD",
        ),
        QuotationLineItem(
            line_id="L2",
            product_id="SYN-ACC-1",
            description="Synthetic detector grid",
            category=LineItemCategory.ACCESSORY,
            quantity=2,
            unit_price=2500.0,
            currency="USD",
        ),
    ]


def submit_quotation(
    service,
    approval_service,
    people,
    quotation_id,
    *,
    status="pass",
    margin="42.0",
    approver="manager",
):
    """Create, decide and submit a quotation, returning the approval task."""

    from tests.fixtures.phase6_helpers import make_decided_state

    loaded = service.create_quotation(
        quotation_id=quotation_id, owner_user_id=people["sales"].user_id
    )
    make_decided_state(loaded.state, status=status, margin=margin)
    add_line_items(loaded.state)
    loaded = service.save_state(loaded, actor=people["sales"].username)
    task = approval_service.submit_for_approval(
        loaded,
        user=people["sales"],
        approver_user_id=people[approver].user_id,
    )
    return task
