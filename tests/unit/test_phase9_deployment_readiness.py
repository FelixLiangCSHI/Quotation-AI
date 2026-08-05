"""Phase 9: Streamlit Cloud deployment readiness.

These tests protect the properties a public demo depends on: a clean-import
surface, deterministic behaviour without any secret, demo-safe storage, the
three documented margin gate outcomes, and customer output that leaks nothing
internal.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from app.commercial_policy import active_commercial_policy
from app.db.session import (
    DEFAULT_DATABASE_URL,
    MEMORY_DATABASE_URL,
    describe_database_mode,
    load_database_settings,
    resolve_demo_database_url,
)
from app.demo_scenarios import MARGIN_GATE_SCENARIOS, build_margin_gate_state
from app.runtime import (
    APP_VERSION,
    apply_streamlit_secrets,
    is_allowed_secret_name,
    runtime_report,
)
from app.workflow_orchestrator import analyse_quotation_lines, judge_quotation

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


# -- clean environment installation ----------------------------------------


def test_requirements_are_pure_python_and_pinned():
    lines = [
        line.strip()
        for line in (REPOSITORY_ROOT / "requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert lines, "requirements.txt must not be empty"
    # Every requirement carries a lower bound, so a clean install is reproducible.
    assert all(">=" in line for line in lines)
    # Packages needing system libraries or a browser download are unavailable
    # on Streamlit Community Cloud and must never be required.
    forbidden = {"weasyprint", "playwright", "psycopg2", "pycairo", "pango"}
    assert not forbidden & {
        re.split(r"[<>=!\[]", line, maxsplit=1)[0].strip().casefold()
        for line in lines
    }


@pytest.mark.parametrize(
    "module_name",
    [
        "app.agents.health",
        "app.audit_export",
        "app.demo_scenarios",
        "app.document_generator",
        "app.email_generator",
        "app.margin_gate",
        "app.quotation_pricing",
        "app.runtime",
        "app.workflow_orchestrator",
    ],
)
def test_modules_import_without_any_configuration(module_name, monkeypatch):
    for name in (
        "AGENT1_PROVIDER",
        "AGENT1_API_KEY",
        "AGENT2_PROVIDER",
        "AGENT3_PROVIDER",
        "AGENT4_PROVIDER",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    assert importlib.import_module(module_name) is not None


def test_no_module_hardcodes_an_absolute_local_path():
    offenders = []
    for path in (REPOSITORY_ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"[\"'][A-Za-z]:\\\\|[\"']/home/|[\"']/Users/", text):
            offenders.append(path.name)

    assert offenders == []


# -- secrets bridging -------------------------------------------------------


def test_allow_listed_secrets_are_promoted_to_the_environment():
    environment: dict[str, str] = {}

    applied = apply_streamlit_secrets(
        {
            "AGENT1_PROVIDER": "openai_compatible",
            "AGENT1_API_KEY": "not-a-real-key",
            "DEMO_DATABASE_MODE": "memory",
        },
        environment,
    )

    assert applied == ("AGENT1_API_KEY", "AGENT1_PROVIDER", "DEMO_DATABASE_MODE")
    assert environment["AGENT1_PROVIDER"] == "openai_compatible"


def test_unknown_and_structured_secrets_are_ignored():
    environment: dict[str, str] = {}

    applied = apply_streamlit_secrets(
        {
            "PATH": "/malicious",
            "AWS_SECRET_ACCESS_KEY": "nope",
            "AGENT9_PROVIDER": "nope",
            "connections": {"nested": "value"},
        },
        environment,
    )

    assert applied == ()
    assert environment == {}
    assert not is_allowed_secret_name("PATH")


def test_an_existing_environment_variable_is_never_overridden():
    environment = {"DEMO_MODE": "false"}

    apply_streamlit_secrets({"DEMO_MODE": "true"}, environment)

    assert environment["DEMO_MODE"] == "false"


def test_missing_secrets_are_not_an_error():
    assert apply_streamlit_secrets(None, {}) == ()
    assert apply_streamlit_secrets({}, {}) == ()


# -- demo-safe database mode -----------------------------------------------


def test_in_memory_demo_mode_requires_no_persistent_storage():
    environment = {"DEMO_DATABASE_MODE": "memory"}

    assert resolve_demo_database_url(environment) == MEMORY_DATABASE_URL
    assert describe_database_mode(environment)["mode"] == "demo (in-memory SQLite)"


def test_temporary_demo_mode_uses_a_throwaway_file():
    environment = {"DEMO_DATABASE_MODE": "temporary_file"}

    url = resolve_demo_database_url(environment)

    assert url.startswith("sqlite+pysqlite:///")
    assert describe_database_mode(environment)["persistence"].startswith(
        "discarded"
    )


def test_local_file_mode_is_still_available():
    environment = {"DEMO_DATABASE_MODE": "local_file"}

    assert resolve_demo_database_url(environment) == DEFAULT_DATABASE_URL


def test_an_unknown_demo_database_mode_falls_back_instead_of_failing():
    environment = {"DEMO_DATABASE_MODE": "nonsense"}

    assert resolve_demo_database_url(environment).startswith("sqlite+pysqlite:///")


def test_a_configured_database_url_is_reported_without_credentials():
    password = "not-a-real-password"
    environment = {
        "DATABASE_URL": (
            f"postgresql+psycopg2://demo:{password}@db.example:5432/quotes"
        )
    }

    description = describe_database_mode(environment)

    assert password not in str(description)
    assert description["target"] == "postgresql+psycopg2"
    assert load_database_settings(environment).is_sqlite is False


# -- startup checks ---------------------------------------------------------


def test_startup_report_defaults_to_demo_mode_with_deterministic_agents():
    report = runtime_report({})

    assert report.version == APP_VERSION
    assert report.application_mode == "demo"
    assert len(report.agents) == 4
    assert all(agent["mode"] == "deterministic" for agent in report.agents)
    assert all(agent["fallback"] == "deterministic" for agent in report.agents)


def test_startup_report_never_contains_a_secret_value():
    environment = {
        "AGENT1_PROVIDER": "openai_compatible",
        "AGENT1_BASE_URL": "https://endpoint.invalid/v1",
        "AGENT1_API_KEY": "super-secret-value",
        "AGENT1_MODEL": "demo-model",
    }

    report = runtime_report(environment)

    assert "super-secret-value" not in str(report)
    assert report.application_mode == "configured API"
    assert report.agents[0]["api_key_present"] is True


def test_an_invalid_agent_provider_does_not_break_startup():
    report = runtime_report({"AGENT2_PROVIDER": "not-a-provider"})

    assert report.agents[1]["mode"] == "deterministic"


# -- demo scenarios ---------------------------------------------------------


@pytest.mark.parametrize(
    "scenario", MARGIN_GATE_SCENARIOS, ids=lambda s: s.scenario_id
)
def test_each_demo_scenario_produces_its_documented_outcome(scenario):
    state = build_margin_gate_state(scenario.scenario_id)

    analysis = analyse_quotation_lines(state)
    decision = judge_quotation(state)

    assert decision.status == scenario.expected_status
    if scenario.expected_status == "blocked":
        assert analysis.gross_margin_percent is None
        assert decision.blocking_reasons
    else:
        assert analysis.gross_margin_percent is not None


def test_the_review_scenario_lands_exactly_on_the_active_threshold():
    state = build_margin_gate_state("margin_review")

    analysis = analyse_quotation_lines(state)
    judge_quotation(state)

    threshold = active_commercial_policy().pass_margin_threshold_percent
    assert float(analysis.gross_margin_percent) == float(threshold)


def test_a_blocked_scenario_cannot_be_approved():
    from app.approval_workflow import available_approval_actions

    state = build_margin_gate_state("margin_blocked")
    analyse_quotation_lines(state)
    judge_quotation(state)

    actions = available_approval_actions(state)

    assert "approve" not in actions
    assert "approve_with_override" not in actions


def test_a_review_scenario_requires_an_override():
    from app.approval_workflow import available_approval_actions

    state = build_margin_gate_state("margin_review")
    analyse_quotation_lines(state)
    judge_quotation(state)

    actions = available_approval_actions(state)

    assert "approve_with_override" in actions
    assert "approve" not in actions


def test_demo_scenarios_use_no_real_customer_or_company_data():
    text = " ".join(
        f"{scenario.customer_name} {scenario.description} "
        + " ".join(line[0] for line in scenario.lines)
        for scenario in MARGIN_GATE_SCENARIOS
    ).casefold()

    assert "sap" not in text
    assert all(
        token in text
        for token in ("synthetic", "example", "sample", "demo")
    )
