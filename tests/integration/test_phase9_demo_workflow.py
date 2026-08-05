"""Phase 9: the demo workflow that Streamlit Cloud must be able to show.

One test drives the documented PASS scenario end to end - creation, pricing,
margin judgement, human approval, email and PDF - through the real service
layer, then proves the customer-facing artefacts carry no internal value.
"""

from __future__ import annotations

import pytest

from app.audit_export import (
    build_customer_quotation_export,
    export_json_bytes,
)
from app.demo_scenarios import build_margin_gate_state
from app.document_generator import generate_quotation_pdf
from app.email_generator import generate_customer_email
from app.quotation_models import ApprovalStatus
from app.workflow_orchestrator import analyse_quotation_lines, judge_quotation

#: Values and words that must never reach a customer-facing artefact.
INTERNAL_TOKENS = (
    "60000",
    "54000",
    "6000.00",
    "40000",
    "35.00",
    "35%",
    "cogs",
    "gross margin",
    "margin threshold",
    "policy version",
    "pol-margin-mvp",
    "review_required",
    "approval reason",
    "prompt",
)

#: The customer JSON export carries a customer-safe event list by design, so
#: only genuinely internal audit artefacts are checked there.
INTERNAL_EXPORT_TOKENS = INTERNAL_TOKENS + (
    "internal_audit",
    "rule_artifact",
    "triggered_rule",
)


def _approved_pass_quotation(service):
    state = build_margin_gate_state("margin_pass")
    analyse_quotation_lines(state)
    decision = judge_quotation(state)
    assert decision.status == "pass"

    quotation_id = state.draft.quotation_id
    service.create_quotation(state=state)
    loaded = service.submit_for_approval(
        service.load_quotation(quotation_id),
        approver_name="Dana Approver",
        approver_role="Sales Manager",
    )
    service.decide_approval(
        loaded,
        action="approve",
        actor_role="Sales Manager",
        actor_name="Dana Approver",
        action_id="phase9-approval",
    )
    return service.load_quotation(quotation_id)


def test_the_pass_scenario_runs_end_to_end_through_the_service_layer(service):
    approved = _approved_pass_quotation(service)

    assert approved.state.approval.status is ApprovalStatus.APPROVED
    assert approved.record.version >= 1
    assert approved.state.quotation_pricing.gross_margin_percent is not None


def test_the_approved_demo_quotation_generates_an_email_and_a_pdf(service):
    approved = _approved_pass_quotation(service)

    email = generate_customer_email(approved.state)
    document = generate_quotation_pdf(approved.state)

    assert email.subject
    assert email.body
    assert document.mime_type == "application/pdf"
    assert len(document.bytes_data) > 1000


@pytest.mark.parametrize("token", INTERNAL_TOKENS)
def test_customer_email_leaks_no_internal_value(service, token):
    approved = _approved_pass_quotation(service)

    email = generate_customer_email(approved.state)
    text = f"{email.subject} {email.body}".casefold()

    assert token.casefold() not in text


@pytest.mark.parametrize("token", INTERNAL_EXPORT_TOKENS)
def test_customer_export_leaks_no_internal_value(service, token):
    approved = _approved_pass_quotation(service)

    payload = export_json_bytes(
        build_customer_quotation_export(approved.state)
    ).decode("utf-8")

    assert token.casefold() not in payload.casefold()


def test_a_blocked_demo_scenario_can_never_produce_a_customer_document(service):
    from app.output_context import OutputGenerationError

    state = build_margin_gate_state("margin_blocked")
    analyse_quotation_lines(state)
    decision = judge_quotation(state)
    assert decision.status == "blocked"

    with pytest.raises(OutputGenerationError):
        generate_quotation_pdf(state)
    with pytest.raises(OutputGenerationError):
        generate_customer_email(state)


def test_the_workflow_completes_with_no_agent_api_configured(service, monkeypatch):
    """Agents 1-4 are optional: the deterministic fallback carries the demo."""

    for index in (1, 2, 3, 4):
        for suffix in ("PROVIDER", "API_KEY", "BASE_URL", "MODEL"):
            monkeypatch.delenv(f"AGENT{index}_{suffix}", raising=False)

    approved = _approved_pass_quotation(service)
    explanation = approved.state.pricing_explanation

    assert explanation is not None
    assert explanation.fallback_used is True
    assert generate_quotation_pdf(approved.state).mime_type == "application/pdf"


def test_an_unreachable_agent_endpoint_falls_back_instead_of_failing(
    service, monkeypatch
):
    monkeypatch.setenv("AGENT2_PROVIDER", "openai_compatible")
    monkeypatch.setenv("AGENT2_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("AGENT2_MODEL", "unreachable-model")
    monkeypatch.setenv("AGENT2_TIMEOUT_SECONDS", "1")

    state = build_margin_gate_state("margin_pass")
    analyse_quotation_lines(state)
    decision = judge_quotation(state)

    # The deterministic decision is unaffected by the unreachable provider.
    assert decision.status == "pass"
    assert state.pricing_explanation is not None
