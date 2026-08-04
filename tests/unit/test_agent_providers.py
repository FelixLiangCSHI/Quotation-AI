from __future__ import annotations

import json

import pytest

from app.agents import (
    Agent1RequirementAgent,
    Agent2PricingNarrativeAgent,
    Agent3EmailWordingAgent,
    Agent4DocumentPlanAgent,
    AgentAuditLog,
    AgentConfigurationError,
    DeterministicProvider,
    DocumentPlanRequest,
    EmailWordingRequest,
    ErrorCategory,
    HttpJsonProvider,
    InvocationStatus,
    MockProvider,
    OpenAICompatibleProvider,
    PricingNarrativeRequest,
    RequirementRequest,
    agent_health_report,
    build_provider,
    load_agent_config,
    load_agent_configs,
)
from app.agents.contracts import AgentProviderError, AgentProviderTimeout


@pytest.fixture
def requirement_request() -> RequirementRequest:
    return RequirementRequest(
        customer_request="Need a DRX system for Germany",
        known_fields={"region": "Germany", "quantity": "2"},
        missing_fields=("currency", "incoterm"),
        candidate_products=("DRX-100",),
    )


@pytest.fixture
def pricing_request() -> PricingNarrativeRequest:
    return PricingNarrativeRequest(
        evidence_lines=("Comparable quotations: 3",),
        analysis_lines=("Recommended unit price: USD 10,000.00",),
        risk_lines=("Limited comparable evidence.",),
        protected_values=("USD 10,000.00",),
    )


@pytest.fixture
def email_request() -> EmailWordingRequest:
    return EmailWordingRequest(
        email_type="internal_approval_request",
        subject="Approval requested: quotation Q-1",
        body="Quotation ID: Q-1\nCustomer: ACME\nTotal: USD 20,000.00",
        protected_values=("Q-1", "ACME", "USD 20,000.00"),
    )


@pytest.fixture
def document_request() -> DocumentPlanRequest:
    return DocumentPlanRequest(
        section_ids=("header", "items", "terms"),
        section_headings={"items": "Quotation Items"},
        customer_safe_facts=("Q-1", "ACME"),
    )


def _agent_env(agent: str, **overrides: str) -> dict[str, str]:
    prefix = agent.upper()
    return {f"{prefix}_{key}": value for key, value in overrides.items()}


# --- deterministic operation ----------------------------------------------


def test_all_agents_run_deterministically_without_api_keys(
    requirement_request, pricing_request, email_request, document_request
):
    environment: dict[str, str] = {}
    outcomes = [
        Agent1RequirementAgent(
            config=load_agent_config("agent1", environment)
        ).run(requirement_request),
        Agent2PricingNarrativeAgent(
            config=load_agent_config("agent2", environment)
        ).run(pricing_request),
        Agent3EmailWordingAgent(
            config=load_agent_config("agent3", environment)
        ).run(email_request),
        Agent4DocumentPlanAgent(
            config=load_agent_config("agent4", environment)
        ).run(document_request),
    ]
    for outcome in outcomes:
        assert outcome.audit.status is InvocationStatus.ACCEPTED
        assert outcome.audit.fallback_used is False
        assert outcome.audit.error_category is ErrorCategory.NONE
    assert outcomes[0].value.missing_questions
    assert "USD 10,000.00" in outcomes[1].value.analysis_explanation
    assert outcomes[2].value.subject.startswith("Approval requested")
    assert [section.section_id for section in outcomes[3].value.sections] == [
        "header",
        "items",
        "terms",
    ]


def test_deterministic_provider_health_is_always_ready():
    health = DeterministicProvider().health_check()
    assert health.configured and health.healthy


# --- provider selection ----------------------------------------------------


@pytest.mark.parametrize(
    "provider_name, expected_type",
    [
        ("deterministic", DeterministicProvider),
        ("mock", MockProvider),
        ("http_json", HttpJsonProvider),
        ("openai_compatible", OpenAICompatibleProvider),
    ],
)
def test_provider_selection(provider_name, expected_type):
    config = load_agent_config(
        "agent1",
        _agent_env(
            "agent1",
            PROVIDER=provider_name,
            BASE_URL="https://example.invalid/v1",
            MODEL="test-model",
        ),
    )
    assert isinstance(build_provider(config), expected_type)


def test_per_agent_provider_configuration():
    environment = {
        **_agent_env("agent1", PROVIDER="deterministic"),
        **_agent_env(
            "agent2",
            PROVIDER="openai_compatible",
            BASE_URL="https://gateway.invalid/v1",
            API_KEY_ENV="CUSTOM_KEY",
            MODEL="gpt-test",
            TIMEOUT_SECONDS="5",
            MAX_RETRIES="2",
            ORGANISATION="org-1",
            PROJECT="proj-1",
        ),
        **_agent_env("agent3", PROVIDER="mock"),
        **_agent_env(
            "agent4", PROVIDER="http_json", BASE_URL="https://plan.invalid"
        ),
    }
    configs = load_agent_configs(environment)
    assert configs["agent1"].provider == "deterministic"
    assert configs["agent2"].provider == "openai_compatible"
    assert configs["agent2"].api_key_env == "CUSTOM_KEY"
    assert configs["agent2"].timeout_seconds == 5.0
    assert configs["agent2"].max_retries == 2
    assert configs["agent2"].organisation == "org-1"
    assert configs["agent2"].project == "proj-1"
    assert configs["agent3"].provider == "mock"
    assert configs["agent4"].provider == "http_json"
    assert configs["agent4"].api_key_env == "AGENT4_API_KEY"


# --- invalid provider configuration ---------------------------------------


def test_unknown_provider_is_rejected():
    with pytest.raises(AgentConfigurationError):
        load_agent_config("agent1", _agent_env("agent1", PROVIDER="magic"))


def test_invalid_timeout_is_rejected():
    with pytest.raises(AgentConfigurationError):
        load_agent_config("agent1", _agent_env("agent1", TIMEOUT_SECONDS="0"))
    with pytest.raises(AgentConfigurationError):
        load_agent_config("agent1", _agent_env("agent1", TIMEOUT_SECONDS="abc"))


def test_unknown_agent_name_is_rejected():
    with pytest.raises(AgentConfigurationError):
        load_agent_config("agent9")


def test_missing_endpoint_falls_back_to_deterministic(requirement_request):
    config = load_agent_config(
        "agent1", _agent_env("agent1", PROVIDER="http_json")
    )
    agent = Agent1RequirementAgent(config=config)
    outcome = agent.run(requirement_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.MISSING_CONFIGURATION
    assert outcome.value.missing_questions


def test_openai_provider_without_api_key_falls_back(pricing_request):
    config = load_agent_config(
        "agent2",
        _agent_env(
            "agent2",
            PROVIDER="openai_compatible",
            BASE_URL="https://gateway.invalid/v1",
            MODEL="gpt-test",
            API_KEY_ENV="DEFINITELY_UNSET_KEY_ENV",
        ),
    )
    outcome = Agent2PricingNarrativeAgent(config=config).run(pricing_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.MISSING_CONFIGURATION


# --- fallback branches -----------------------------------------------------


def test_timeout_falls_back(requirement_request):
    provider = MockProvider(
        {"extract_requirements": AgentProviderTimeout("slow")}
    )
    outcome = Agent1RequirementAgent(
        config=load_agent_config("agent1", _agent_env("agent1", PROVIDER="mock")),
        provider=provider,
    ).run(requirement_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.TIMEOUT
    assert outcome.value == Agent1RequirementAgent.deterministic_baseline(
        requirement_request
    )


def test_malformed_json_falls_back(pricing_request):
    provider = MockProvider({"summarise_pricing_evidence": "not-json{"})
    outcome = Agent2PricingNarrativeAgent(
        config=load_agent_config("agent2", _agent_env("agent2", PROVIDER="mock")),
        provider=provider,
    ).run(pricing_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.INVALID_JSON


def test_schema_failure_falls_back(document_request):
    provider = MockProvider({"plan_document": {"sections": "wrong-type"}})
    outcome = Agent4DocumentPlanAgent(
        config=load_agent_config("agent4", _agent_env("agent4", PROVIDER="mock")),
        provider=provider,
    ).run(document_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.SCHEMA_VALIDATION


def test_unknown_schema_field_is_rejected(email_request):
    provider = MockProvider(
        {
            "rewrite_email": {
                "email_type": email_request.email_type,
                "subject": email_request.subject,
                "body": email_request.body,
                "approval_status": "APPROVED",
            }
        }
    )
    outcome = Agent3EmailWordingAgent(
        config=load_agent_config("agent3", _agent_env("agent3", PROVIDER="mock")),
        provider=provider,
    ).run(email_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.SCHEMA_VALIDATION


def test_provider_error_falls_back(email_request):
    provider = MockProvider({"rewrite_email": AgentProviderError("boom")})
    outcome = Agent3EmailWordingAgent(
        config=load_agent_config("agent3", _agent_env("agent3", PROVIDER="mock")),
        provider=provider,
    ).run(email_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.PROVIDER_ERROR


# --- protected values ------------------------------------------------------


def test_protected_value_loss_falls_back(email_request):
    provider = MockProvider(
        {
            "rewrite_email": {
                "email_type": email_request.email_type,
                "subject": "Approval requested",
                "body": "Please approve this quotation.",
            }
        }
    )
    outcome = Agent3EmailWordingAgent(
        config=load_agent_config("agent3", _agent_env("agent3", PROVIDER="mock")),
        provider=provider,
    ).run(email_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.PROTECTED_FIELD
    assert outcome.value.body == email_request.body


def test_email_type_change_is_rejected(email_request):
    provider = MockProvider(
        {
            "rewrite_email": {
                "email_type": "customer_quotation",
                "subject": email_request.subject,
                "body": email_request.body,
            }
        }
    )
    outcome = Agent3EmailWordingAgent(
        config=load_agent_config("agent3", _agent_env("agent3", PROVIDER="mock")),
        provider=provider,
    ).run(email_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.BUSINESS_RULE


def test_agent2_cannot_assert_commercial_decisions(pricing_request):
    provider = MockProvider(
        {
            "summarise_pricing_evidence": {
                "evidence_summary": "USD 10,000.00 evidence",
                "analysis_explanation": (
                    "USD 10,000.00 with an extra discount of 25 percent"
                ),
                "risks": [],
            }
        }
    )
    outcome = Agent2PricingNarrativeAgent(
        config=load_agent_config("agent2", _agent_env("agent2", PROVIDER="mock")),
        provider=provider,
    ).run(pricing_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.BUSINESS_RULE


def test_agent4_cannot_drop_or_invent_sections(document_request):
    provider = MockProvider(
        {
            "plan_document": {
                "sections": [
                    {"section_id": "header", "heading": "Header", "narrative": ""},
                    {"section_id": "pricing", "heading": "Pricing", "narrative": ""},
                ],
                "customer_safe_summary": "Q-1 ACME",
            }
        }
    )
    outcome = Agent4DocumentPlanAgent(
        config=load_agent_config("agent4", _agent_env("agent4", PROVIDER="mock")),
        provider=provider,
    ).run(document_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.BUSINESS_RULE


def test_agent4_cannot_expose_internal_fields(document_request):
    provider = MockProvider(
        {
            "plan_document": {
                "sections": [
                    {"section_id": "header", "heading": "Header", "narrative": ""},
                    {"section_id": "items", "heading": "Items", "narrative": ""},
                    {
                        "section_id": "terms",
                        "heading": "Terms",
                        "narrative": "Internal gross margin percent noted here.",
                    },
                ],
                "customer_safe_summary": "Q-1 ACME",
            }
        }
    )
    outcome = Agent4DocumentPlanAgent(
        config=load_agent_config("agent4", _agent_env("agent4", PROVIDER="mock")),
        provider=provider,
    ).run(document_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.BUSINESS_RULE


# --- mock success paths ----------------------------------------------------


def test_mock_success_for_each_agent(
    requirement_request, pricing_request, email_request, document_request
):
    agent1 = Agent1RequirementAgent(
        config=load_agent_config("agent1", _agent_env("agent1", PROVIDER="mock")),
        provider=MockProvider(
            {
                "extract_requirements": {
                    "requirements": [
                        {
                            "field_name": "region",
                            "value": "Germany",
                            "confidence": 0.9,
                        }
                    ],
                    "product_interpretation": "DRX system for Germany",
                    "missing_questions": ["Which currency should be used?"],
                    "recommendation_rationale": "Matches the requested region.",
                }
            }
        ),
    ).run(requirement_request)
    agent2 = Agent2PricingNarrativeAgent(
        config=load_agent_config("agent2", _agent_env("agent2", PROVIDER="mock")),
        provider=MockProvider(
            {
                "summarise_pricing_evidence": {
                    "evidence_summary": "Three comparables support USD 10,000.00.",
                    "analysis_explanation": (
                        "The deterministic engine produced USD 10,000.00."
                    ),
                    "risks": ["Limited comparable evidence."],
                }
            }
        ),
    ).run(pricing_request)
    agent3 = Agent3EmailWordingAgent(
        config=load_agent_config("agent3", _agent_env("agent3", PROVIDER="mock")),
        provider=MockProvider(
            {
                "rewrite_email": {
                    "email_type": "internal_approval_request",
                    "subject": "Approval requested: quotation Q-1",
                    "body": (
                        "Hello,\nQuotation ID: Q-1\nCustomer: ACME\n"
                        "Total: USD 20,000.00"
                    ),
                }
            }
        ),
    ).run(email_request)
    agent4 = Agent4DocumentPlanAgent(
        config=load_agent_config("agent4", _agent_env("agent4", PROVIDER="mock")),
        provider=MockProvider(
            {
                "plan_document": {
                    "sections": [
                        {"section_id": "header", "heading": "Header", "narrative": ""},
                        {
                            "section_id": "items",
                            "heading": "Quotation Items",
                            "narrative": "",
                        },
                        {"section_id": "terms", "heading": "Terms", "narrative": ""},
                    ],
                    "customer_safe_summary": "Q-1 ACME",
                }
            }
        ),
    ).run(document_request)

    for outcome in (agent1, agent2, agent3, agent4):
        assert outcome.fallback_used is False
        assert outcome.audit.status is InvocationStatus.ACCEPTED
    assert agent1.value.recommendation_rationale.startswith("Matches")
    assert "USD 10,000.00" in agent2.value.evidence_summary
    assert agent3.value.body.startswith("Hello")
    assert agent4.value.sections[1].heading == "Quotation Items"


# --- HTTP providers with fake transports (no network) ----------------------


class _FakeTransport:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.requests: list[dict] = []

    def post_json(self, *, url, payload, headers, timeout_seconds):
        self.requests.append(
            {
                "url": url,
                "payload": payload,
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        if self._error is not None:
            raise self._error
        return self._response


def test_http_json_provider_success(monkeypatch, pricing_request):
    monkeypatch.setenv("AGENT2_API_KEY", "test-secret")
    config = load_agent_config(
        "agent2",
        _agent_env(
            "agent2", PROVIDER="http_json", BASE_URL="https://pricing.invalid/v1"
        ),
    )
    transport = _FakeTransport(
        {
            "output": {
                "evidence_summary": "USD 10,000.00 supported by comparables.",
                "analysis_explanation": "Deterministic result USD 10,000.00.",
                "risks": [],
            },
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        }
    )
    agent = Agent2PricingNarrativeAgent(
        config=config, provider=HttpJsonProvider(config, transport=transport)
    )
    outcome = agent.run(pricing_request)
    assert outcome.fallback_used is False
    assert outcome.audit.usage["prompt_tokens"] == 12
    assert transport.requests[0]["timeout_seconds"] == config.timeout_seconds
    assert "test-secret" not in json.dumps(outcome.audit.to_dict())


def test_openai_compatible_provider_success(monkeypatch, requirement_request):
    monkeypatch.setenv("AGENT1_API_KEY", "test-secret")
    config = load_agent_config(
        "agent1",
        _agent_env(
            "agent1",
            PROVIDER="openai_compatible",
            BASE_URL="https://gateway.invalid/v1",
            MODEL="gpt-test",
        ),
    )
    transport = _FakeTransport(
        {
            "model": "gpt-test",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "requirements": [],
                                "product_interpretation": "DRX system",
                                "missing_questions": ["Which currency?"],
                                "recommendation_rationale": "Region match.",
                            }
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 42},
        }
    )
    agent = Agent1RequirementAgent(
        config=config, provider=OpenAICompatibleProvider(config, transport=transport)
    )
    outcome = agent.run(requirement_request)
    assert outcome.fallback_used is False
    assert outcome.audit.model == "gpt-test"
    assert outcome.audit.usage["total_tokens"] == 42
    assert transport.requests[0]["url"].endswith("/chat/completions")


def test_openai_compatible_transport_timeout_falls_back(
    monkeypatch, requirement_request
):
    monkeypatch.setenv("AGENT1_API_KEY", "test-secret")
    config = load_agent_config(
        "agent1",
        _agent_env(
            "agent1",
            PROVIDER="openai_compatible",
            BASE_URL="https://gateway.invalid/v1",
            MODEL="gpt-test",
            MAX_RETRIES="1",
        ),
    )
    transport = _FakeTransport(error=AgentProviderTimeout("timed out"))
    agent = Agent1RequirementAgent(
        config=config, provider=OpenAICompatibleProvider(config, transport=transport)
    )
    outcome = agent.run(requirement_request)
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.TIMEOUT
    assert len(transport.requests) == 2


# --- audit and health ------------------------------------------------------


def test_audit_metadata_is_recorded_without_secrets(
    monkeypatch, requirement_request
):
    monkeypatch.setenv("AGENT1_API_KEY", "test-secret")
    audit_log = AgentAuditLog()
    config = load_agent_config(
        "agent1",
        _agent_env(
            "agent1",
            PROVIDER="mock",
            MODEL="mock-model",
            PROMPT_TEMPLATE_VERSION="v3",
        ),
    )
    provider = MockProvider(
        {
            "extract_requirements": {
                "requirements": [],
                "product_interpretation": "DRX",
                "missing_questions": [],
                "recommendation_rationale": "ok",
            }
        },
        usage={"prompt_tokens": 5, "api_key": "test-secret"},
    )
    outcome = Agent1RequirementAgent(
        config=config, provider=provider, audit_log=audit_log
    ).run(requirement_request)
    record = audit_log.records[0]
    assert record is outcome.audit
    data = record.to_dict()
    assert data["agent_name"] == "agent1"
    assert data["provider"] == "mock"
    assert data["status"] == "accepted"
    assert data["fallback_used"] is False
    assert data["prompt_template_version"] == "v3"
    assert data["error_category"] == "none"
    assert data["usage"] == {"prompt_tokens": 5}
    assert data["started_at"] and data["ended_at"]
    assert "test-secret" not in json.dumps(data)


def test_health_report_never_exposes_secrets(monkeypatch):
    monkeypatch.setenv("AGENT2_API_KEY", "test-secret")
    environment = {
        **_agent_env(
            "agent2",
            PROVIDER="openai_compatible",
            BASE_URL="https://gateway.invalid/v1",
            MODEL="gpt-test",
        ),
        "AGENT2_API_KEY": "test-secret",
    }
    report = agent_health_report(environment)
    serialised = json.dumps(report)
    assert "test-secret" not in serialised
    assert report["agent1"]["healthy"] is True
    assert report["agent2"]["api_key_present"] is True
    assert report["agent2"]["api_key_env"] == "AGENT2_API_KEY"
    assert all(entry["fallback_mode"] == "deterministic" for entry in report.values())


def test_health_report_reports_invalid_configuration():
    report = agent_health_report(_agent_env("agent3", PROVIDER="unsupported"))
    assert report["agent3"]["healthy"] is False
    assert report["agent1"]["healthy"] is True


def test_config_describe_hides_key_value(monkeypatch):
    monkeypatch.setenv("AGENT4_API_KEY", "test-secret")
    config = load_agent_config("agent4", _agent_env("agent4", PROVIDER="mock"))
    described = config.describe()
    assert described["api_key_present"] is True
    assert "test-secret" not in json.dumps(described)
