"""Phase 7 unit tests: Agent 3 wording, recipients and delivery providers."""

from __future__ import annotations

import smtplib

import pytest

from app.agents.agents import Agent3EmailWordingAgent
from app.agents.config import AgentProviderConfig
from app.agents.contracts import AgentProviderTimeout
from app.agents.providers.mock import MockProvider
from app.emailing.composition import (
    EmailFacts,
    EmailLineItem,
    compose_email,
    customer_boundary_problems,
)
from app.emailing.config import EmailConfigurationError, load_email_config
from app.emailing.contracts import (
    DeliveryErrorCategory,
    EmailAudience,
    EmailType,
    OutboundEmail,
    PermanentDeliveryError,
    RecipientValidationError,
)
from app.emailing.providers import (
    ConsoleEmailProvider,
    MicrosoftGraphEmailProvider,
    SMTPEmailProvider,
    build_delivery_provider,
)
from app.emailing.recipients import validate_recipients
from tests.fixtures.phase7_helpers import INTERNAL_DOMAIN, email_config

FACTS = EmailFacts(
    quotation_id="Q7-1",
    quotation_version=3,
    customer_name="Northwind Medical",
    currency="USD",
    line_items=(
        EmailLineItem(
            product_id="SYN-MAIN-1",
            description="Synthetic imaging system",
            quantity=1,
            unit_price="100000.00",
            extended_price="100000.00",
        ),
    ),
    total_revenue="100000.00",
    gross_margin_percent="42.00",
    threshold_percent="35.00",
    decision_status="pass",
    approval_status="pending_review",
    approver_name="Mia Manager",
    approval_task_id=7,
    task_reference="TASK-ABC",
    incoterm="DAP",
    quotation_date="2026-01-01",
    reminder_due_at="2026-01-03T00:00:00+00:00",
)


def _agent(response) -> Agent3EmailWordingAgent:
    return Agent3EmailWordingAgent(
        config=AgentProviderConfig(agent_name="agent3", provider="mock"),
        provider=MockProvider({"rewrite_email": response}),
    )


# -- Agent 3 ------------------------------------------------------------


def test_deterministic_mode_uses_the_template():
    composed = compose_email(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        facts=FACTS,
        include_margin=True,
    )
    assert composed.fallback_used is True
    assert composed.agent_provider == "deterministic"
    assert "Q7-1" in composed.body
    assert "42.00" in composed.body


def test_a_valid_ai_rewrite_is_accepted():
    deterministic = compose_email(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        facts=FACTS,
        include_margin=True,
    )
    rewritten = {
        "email_type": EmailType.APPROVAL_REQUEST.value,
        "subject": "Please review quotation Q7-1 v3",
        "body": "Hello,\n\n" + deterministic.body,
    }
    composed = compose_email(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        facts=FACTS,
        include_margin=True,
        agent=_agent(rewritten),
    )
    assert composed.fallback_used is False
    assert composed.subject == "Please review quotation Q7-1 v3"


def test_a_provider_timeout_falls_back_to_the_template():
    composed = compose_email(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        facts=FACTS,
        include_margin=True,
        agent=_agent(AgentProviderTimeout("timed out")),
    )
    assert composed.fallback_used is True
    assert composed.fallback_reason == "timeout"
    assert "Q7-1" in composed.body


def test_a_malformed_response_falls_back_to_the_template():
    composed = compose_email(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        facts=FACTS,
        include_margin=True,
        agent=_agent("{not json"),
    )
    assert composed.fallback_used is True
    assert composed.fallback_reason == "invalid_json"


def test_a_contradicted_protected_value_falls_back_to_the_template():
    composed = compose_email(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        facts=FACTS,
        include_margin=True,
        agent=_agent(
            {
                "email_type": EmailType.APPROVAL_REQUEST.value,
                "subject": "Approval requested",
                "body": (
                    "Quotation Q7-1 version 3 for Northwind Medical, total "
                    "USD 95000.00, margin 55.00%."
                ),
            }
        ),
    )
    assert composed.fallback_used is True
    assert composed.fallback_reason == "protected_field"
    assert "100000.00" in composed.body


def test_a_customer_rewrite_that_leaks_internal_terms_is_discarded():
    facts = EmailFacts(
        **{**FACTS.__dict__, "approval_status": "approved"}
    )
    composed = compose_email(
        email_type=EmailType.CUSTOMER_QUOTATION,
        audience=EmailAudience.CUSTOMER,
        facts=facts,
        agent=_agent(
            {
                "email_type": EmailType.CUSTOMER_QUOTATION.value,
                "subject": "Quotation Q7-1 for Northwind Medical",
                "body": (
                    "Dear Northwind Medical, quotation Q7-1 version 3, "
                    "SYN-MAIN-1 Synthetic imaging system qty 1 at USD "
                    "100000.00, extended USD 100000.00, total USD "
                    "100000.00, Incoterm DAP, date 2026-01-01. Our gross "
                    "margin on this deal is healthy."
                ),
            }
        ),
    )
    assert composed.fallback_used is True
    assert composed.fallback_reason == "customer_boundary"


def test_the_customer_template_never_contains_internal_terms():
    facts = EmailFacts(**{**FACTS.__dict__, "approval_status": "approved"})
    composed = compose_email(
        email_type=EmailType.CUSTOMER_QUOTATION,
        audience=EmailAudience.CUSTOMER,
        facts=facts,
    )
    assert customer_boundary_problems(composed.body) == ()
    assert "42.00" not in composed.body
    assert "35.00" not in composed.body


def test_review_required_and_pass_wording_differ():
    passing = compose_email(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        facts=FACTS,
        include_margin=True,
    )
    review_facts = EmailFacts(
        **{**FACTS.__dict__, "decision_status": "review_required"}
    )
    review = compose_email(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        facts=review_facts,
        include_margin=True,
    )
    assert "Decision: PASS" in passing.body
    assert "Decision: REVIEW_REQUIRED" in review.body
    assert "override approval" in review.body


def test_margin_is_omitted_for_an_unauthorised_recipient():
    composed = compose_email(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        facts=FACTS,
        include_margin=False,
    )
    assert "Gross margin" not in composed.body
    assert "Policy threshold" not in composed.body


# -- recipients ---------------------------------------------------------


def test_an_empty_recipient_list_is_refused():
    with pytest.raises(RecipientValidationError):
        validate_recipients(
            (),
            email_type=EmailType.APPROVAL_REQUEST,
            audience=EmailAudience.INTERNAL,
            config=email_config(),
        )


def test_a_malformed_address_is_refused():
    with pytest.raises(RecipientValidationError):
        validate_recipients(
            ("not-an-address",),
            email_type=EmailType.APPROVAL_REQUEST,
            audience=EmailAudience.INTERNAL,
            config=email_config(),
        )


def test_an_internal_email_may_not_go_to_an_external_domain():
    with pytest.raises(RecipientValidationError):
        validate_recipients(
            ("someone@competitor.example",),
            email_type=EmailType.APPROVAL_REQUEST,
            audience=EmailAudience.INTERNAL,
            config=email_config(),
        )


def test_a_customer_audience_is_refused_for_an_internal_email_type():
    with pytest.raises(RecipientValidationError):
        validate_recipients
        validate_recipients(
            ("buyer@customer.example",),
            email_type=EmailType.APPROVAL_REQUEST,
            audience=EmailAudience.CUSTOMER,
            config=email_config(),
        )


def test_customer_delivery_can_be_disabled_by_configuration():
    with pytest.raises(RecipientValidationError):
        validate_recipients(
            ("buyer@customer.example",),
            email_type=EmailType.CUSTOMER_QUOTATION,
            audience=EmailAudience.CUSTOMER,
            config=email_config(allow_customer_delivery=False),
        )


# -- configuration ------------------------------------------------------


def test_configuration_is_read_from_the_environment_and_hides_secrets():
    config = load_email_config(
        {
            "EMAIL_DELIVERY_PROVIDER": "smtp",
            "EMAIL_SENDER_ADDRESS": f"bot@{INTERNAL_DOMAIN}",
            "EMAIL_INTERNAL_DOMAINS": INTERNAL_DOMAIN,
            "SMTP_HOST": "smtp.internal.invalid",
            "SMTP_PASSWORD_ENV": "MY_SMTP_PASSWORD",
            "APPROVAL_REMINDER_DELAY_HOURS": "48",
        }
    )
    described = config.describe()
    assert config.smtp.password_env == "MY_SMTP_PASSWORD"
    assert "password" not in {
        key for key in described["smtp"] if key == "password"
    }
    assert described["smtp"]["password_present"] is False


def test_an_unknown_provider_is_refused():
    with pytest.raises(EmailConfigurationError):
        load_email_config({"EMAIL_DELIVERY_PROVIDER": "carrier-pigeon"})


# -- delivery providers -------------------------------------------------


def _message(**overrides) -> OutboundEmail:
    defaults = dict(
        email_type=EmailType.APPROVAL_REQUEST,
        audience=EmailAudience.INTERNAL,
        sender=f"bot@{INTERNAL_DOMAIN}",
        recipients=(f"mia@{INTERNAL_DOMAIN}",),
        subject="Approval requested",
        body="Please review.",
        quotation_id="Q7-1",
        quotation_version=3,
    )
    defaults.update(overrides)
    return OutboundEmail(**defaults)


def test_the_console_provider_captures_the_message():
    provider = ConsoleEmailProvider()
    result = provider.send(message=_message(), idempotency_key="key-1")
    assert result.delivered is True
    assert provider.sent[0][0] == "key-1"


def test_the_console_provider_deduplicates_an_idempotency_key():
    provider = ConsoleEmailProvider()
    provider.send(message=_message(), idempotency_key="key-1")
    second = provider.send(message=_message(), idempotency_key="key-1")
    assert second.deduplicated is True
    assert len(provider.sent) == 1


class _FakeSMTP:
    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.messages = []
        self.started_tls = False
        _FakeSMTP.instances.append(self)

    instances: list["_FakeSMTP"] = []

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.credentials = (username, password)

    def send_message(self, message, from_addr=None, to_addrs=None):
        self.messages.append((message, from_addr, tuple(to_addrs or ())))

    def quit(self):
        self.quit_called = True


def test_the_smtp_provider_sends_through_its_client(monkeypatch):
    _FakeSMTP.instances = []
    config = load_email_config(
        {
            "EMAIL_DELIVERY_PROVIDER": "smtp",
            "SMTP_HOST": "smtp.internal.invalid",
            "SMTP_USERNAME": "bot",
            "SMTP_PASSWORD_ENV": "TEST_SMTP_PASSWORD",
        }
    )
    provider = SMTPEmailProvider(
        config.smtp,
        environment={"TEST_SMTP_PASSWORD": "not-a-real-password"},
        client_factory=_FakeSMTP,
    )
    result = provider.send(message=_message(), idempotency_key="key-2")
    assert result.delivered is True
    client = _FakeSMTP.instances[0]
    assert client.started_tls is True
    assert client.messages[0][1] == f"bot@{INTERNAL_DOMAIN}"


def test_an_smtp_disconnect_is_reported_as_transient():
    def _factory(*args, **kwargs):
        raise smtplib.SMTPServerDisconnected("gone")

    config = load_email_config({"SMTP_HOST": "smtp.internal.invalid"})
    provider = SMTPEmailProvider(config.smtp, client_factory=_factory)
    with pytest.raises(Exception) as error:
        provider.send(message=_message(), idempotency_key="key-3")
    assert error.value.category is DeliveryErrorCategory.TRANSIENT


def test_an_unconfigured_smtp_host_is_a_permanent_configuration_error():
    config = load_email_config({})
    provider = SMTPEmailProvider(config.smtp)
    with pytest.raises(PermanentDeliveryError) as error:
        provider.send(message=_message(), idempotency_key="key-4")
    assert error.value.category is DeliveryErrorCategory.CONFIGURATION


def test_the_graph_adapter_is_configuration_gated_but_complete():
    config = load_email_config({})
    provider = MicrosoftGraphEmailProvider(config.graph)
    assert provider.health_check().configured is False
    with pytest.raises(PermanentDeliveryError):
        provider.send(message=_message(), idempotency_key="key-5")

    enabled = load_email_config(
        {"GRAPH_ENABLED": "true", "GRAPH_SENDER_USER_ID": "bot"}
    )
    gated = MicrosoftGraphEmailProvider(
        enabled.graph, environment={"GRAPH_TENANT_ID": "tenant"}
    )
    with pytest.raises(PermanentDeliveryError) as error:
        gated.send(message=_message(), idempotency_key="key-6")
    assert "GRAPH_CLIENT_ID" in str(error.value)

    payload = gated.build_payload(_message())
    assert payload["message"]["toRecipients"][0]["emailAddress"]["address"] == (
        f"mia@{INTERNAL_DOMAIN}"
    )


def test_the_configured_provider_is_built_from_configuration():
    assert build_delivery_provider(
        load_email_config({})
    ).provider_name == "console"
    assert build_delivery_provider(
        load_email_config({"EMAIL_DELIVERY_PROVIDER": "smtp"})
    ).provider_name == "smtp"
    assert build_delivery_provider(
        load_email_config({"EMAIL_DELIVERY_PROVIDER": "microsoft_graph"})
    ).provider_name == "microsoft_graph"
