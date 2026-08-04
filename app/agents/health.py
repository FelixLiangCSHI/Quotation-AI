"""Secret-free provider health checks for Agents 1-4."""

from __future__ import annotations

from typing import Mapping

from app.agents.config import (
    AGENT_NAMES,
    AgentConfigurationError,
    load_agent_config,
)
from app.agents.providers import build_provider


def agent_health_report(
    environment: Mapping[str, str] | None = None,
) -> dict[str, dict[str, object]]:
    """Return a per-agent readiness report that never exposes secrets."""

    report: dict[str, dict[str, object]] = {}
    for agent_name in AGENT_NAMES:
        try:
            config = load_agent_config(agent_name, environment)
        except AgentConfigurationError as error:
            report[agent_name] = {
                "agent_name": agent_name,
                "provider": "deterministic",
                "configured": False,
                "healthy": False,
                "detail": str(error),
                "fallback_mode": "deterministic",
            }
            continue
        try:
            provider = build_provider(config)
            health = provider.health_check()
            entry: dict[str, object] = {
                "agent_name": agent_name,
                "provider": health.provider_name,
                "configured": health.configured,
                "healthy": health.healthy,
                "detail": health.detail,
                "model": health.model,
                "base_url": health.base_url,
            }
        except Exception as error:  # noqa: BLE001 - health must never raise
            entry = {
                "agent_name": agent_name,
                "provider": config.provider,
                "configured": False,
                "healthy": False,
                "detail": type(error).__name__,
            }
        entry["api_key_env"] = config.api_key_env
        entry["api_key_present"] = config.resolve_api_key(environment) is not None
        entry["prompt_template_version"] = config.prompt_template_version
        entry["fallback_mode"] = "deterministic"
        report[agent_name] = entry
    return report
