"""Runtime bootstrap for the Streamlit Cloud demo (Phase 9).

Streamlit Community Cloud supplies configuration through ``st.secrets`` rather
than through process environment variables, and it provides no guaranteed
persistent local storage. This module bridges both gaps without introducing any
infrastructure:

* :func:`apply_streamlit_secrets` copies flat, allow-listed secret keys into
  ``os.environ`` **before** any configuration module reads them, so the existing
  environment-driven configuration keeps working unchanged.
* :func:`runtime_report` produces a secret-free startup summary that the UI can
  display: application version, active mode, database mode and the provider
  status of Agents 1-4.

Nothing here is mandatory. When no secret is present the application stays in
its deterministic demo defaults and never fails.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

APP_VERSION = "0.9.0"
APP_PHASE = "phase-9"

#: Secret names that may be copied into the process environment. Only
#: configuration switches are accepted; nothing else is read from secrets.
_ALLOWED_SECRET_NAMES = frozenset(
    {
        "DEMO_MODE",
        "SHOW_INTERNAL_COSTS",
        "ENABLE_LLM",
        "PRICING_DATA_MODE",
        "DATABASE_URL",
        "DATABASE_ECHO",
        "DEMO_DATABASE_MODE",
        "EMAIL_DELIVERY_PROVIDER",
        "EMAIL_SENDER_ADDRESS",
        "EMAIL_INTERNAL_DOMAINS",
        "EMAIL_ALLOW_CUSTOMER_DELIVERY",
        "AUTH_MAX_FAILED_LOGINS",
    }
)

#: ``AGENT<n>_<SETTING>`` secrets are accepted for the four optional agents.
_AGENT_SECRET_PATTERN = re.compile(
    r"^AGENT[1-4]_(PROVIDER|API_KEY|API_KEY_ENV|BASE_URL|MODEL"
    r"|TIMEOUT_SECONDS|MAX_RETRIES|ORGANISATION|PROJECT"
    r"|PROMPT_TEMPLATE_VERSION)$"
)


def is_allowed_secret_name(name: str) -> bool:
    """Return whether a secret key may be promoted to an environment variable."""

    return name in _ALLOWED_SECRET_NAMES or bool(
        _AGENT_SECRET_PATTERN.match(name)
    )


def apply_streamlit_secrets(
    secrets: Mapping[str, Any] | None,
    environment: dict[str, str] | None = None,
) -> tuple[str, ...]:
    """Copy allow-listed secrets into the environment.

    Existing environment variables always win, so a local ``.env`` or a shell
    export is never overridden by a deployment secret. Values are stringified;
    nested sections are ignored because the configuration layer is flat.

    Returns the sorted names of the variables that were set. Values are never
    returned or logged.
    """

    target = os.environ if environment is None else environment
    if not secrets:
        return ()
    applied: list[str] = []
    for name in list(secrets):
        key = str(name).strip()
        if not is_allowed_secret_name(key) or key in target:
            continue
        try:
            value = secrets[name]
        except Exception:  # noqa: BLE001 - a missing secret must never raise
            continue
        if value is None or isinstance(value, (Mapping, list, tuple)):
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value).strip()
        if not text:
            continue
        target[key] = text
        applied.append(key)
    return tuple(sorted(applied))


def bootstrap_from_streamlit() -> tuple[str, ...]:
    """Apply ``st.secrets`` when Streamlit is running, otherwise do nothing."""

    try:  # pragma: no cover - depends on the Streamlit runtime
        import streamlit as st

        secrets = st.secrets
    except Exception:  # noqa: BLE001 - no secrets file is the normal demo case
        return ()
    try:
        return apply_streamlit_secrets(secrets)
    except Exception:  # noqa: BLE001 - startup must never fail on secrets
        return ()


@dataclass(frozen=True)
class RuntimeReport:
    """A secret-free startup summary."""

    version: str
    phase: str
    application_mode: str
    pricing_data_mode: str
    database_mode: str
    database_target: str
    database_persistence: str
    agents: tuple[dict[str, Any], ...]

    @property
    def configured_agent_count(self) -> int:
        return sum(1 for agent in self.agents if agent["mode"] != "deterministic")


def application_mode(environment: Mapping[str, str] | None = None) -> str:
    """``demo`` unless at least one agent is pointed at a configured API."""

    from app.agents.config import AGENT_NAMES, load_agent_config

    values = os.environ if environment is None else environment
    for agent_name in AGENT_NAMES:
        try:
            config = load_agent_config(agent_name, values)
        except Exception:  # noqa: BLE001 - invalid config falls back to demo
            continue
        if config.provider not in {"deterministic", "mock"}:
            return "configured API"
    return "demo"


def runtime_report(environment: Mapping[str, str] | None = None) -> RuntimeReport:
    """Build the startup report. This function never raises and never leaks."""

    from app.agents.health import agent_health_report
    from app.config import PRICING_DATA_MODE
    from app.db.session import describe_database_mode

    values = os.environ if environment is None else environment
    try:
        health = agent_health_report(values)
    except Exception:  # noqa: BLE001 - health must never break startup
        health = {}

    agents: list[dict[str, Any]] = []
    for index, agent_name in enumerate(("agent1", "agent2", "agent3", "agent4"), 1):
        entry = health.get(agent_name, {})
        provider = str(entry.get("provider") or "deterministic")
        configured = bool(entry.get("configured"))
        healthy = bool(entry.get("healthy"))
        if provider in {"deterministic", "mock"} or not configured:
            mode = "deterministic"
        elif healthy:
            mode = "configured"
        else:
            mode = "configured (unreachable, deterministic fallback)"
        agents.append(
            {
                "label": f"Agent {index}",
                "agent_name": agent_name,
                "provider": provider,
                "model": entry.get("model") or "",
                "api_key_present": bool(entry.get("api_key_present")),
                "mode": mode,
                "fallback": "deterministic",
            }
        )

    database = describe_database_mode(values)
    return RuntimeReport(
        version=APP_VERSION,
        phase=APP_PHASE,
        application_mode=application_mode(values),
        pricing_data_mode=PRICING_DATA_MODE,
        database_mode=database["mode"],
        database_target=database["target"],
        database_persistence=database["persistence"],
        agents=tuple(agents),
    )
