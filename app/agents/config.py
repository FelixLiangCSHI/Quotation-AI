"""Per-agent provider configuration.

Configuration is read from environment variables only. API keys are never
stored in the configuration object; only the *name* of the environment
variable holding the key is kept, and it is resolved at call time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

AGENT_NAMES = ("agent1", "agent2", "agent3", "agent4")

SUPPORTED_PROVIDERS = frozenset(
    {"deterministic", "mock", "http_json", "openai_compatible"}
)
PROVIDERS_REQUIRING_ENDPOINT = frozenset({"http_json", "openai_compatible"})

DEFAULT_PROVIDER = "deterministic"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 0


class AgentConfigurationError(ValueError):
    """Raised when an agent's environment configuration cannot be used."""


@dataclass(frozen=True)
class AgentProviderConfig:
    agent_name: str
    provider: str = DEFAULT_PROVIDER
    base_url: str | None = None
    api_key_env: str | None = None
    model: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    organisation: str | None = None
    project: str | None = None
    prompt_template_version: str = "v1"

    @property
    def requires_remote_endpoint(self) -> bool:
        return self.provider in PROVIDERS_REQUIRING_ENDPOINT

    def resolve_api_key(
        self, environment: Mapping[str, str] | None = None
    ) -> str | None:
        """Resolve the API key at call time. Never cached, never logged."""

        if not self.api_key_env:
            return None
        values = os.environ if environment is None else environment
        key = values.get(self.api_key_env)
        return key or None

    def describe(self) -> dict[str, object]:
        """Secret-free description suitable for logs, UI and audit records."""

        return {
            "agent_name": self.agent_name,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "api_key_present": self.resolve_api_key() is not None,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "organisation": self.organisation,
            "project": self.project,
            "prompt_template_version": self.prompt_template_version,
        }


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _float_value(name: str, raw: str | None, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise AgentConfigurationError(f"{name} must be a number") from error
    if value <= 0:
        raise AgentConfigurationError(f"{name} must be greater than zero")
    return value


def _int_value(name: str, raw: str | None, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise AgentConfigurationError(f"{name} must be an integer") from error
    if value < 0:
        raise AgentConfigurationError(f"{name} must not be negative")
    return value


def load_agent_config(
    agent_name: str,
    environment: Mapping[str, str] | None = None,
) -> AgentProviderConfig:
    """Load one agent's configuration from ``AGENTn_*`` variables."""

    normalized_agent = agent_name.strip().casefold()
    if normalized_agent not in AGENT_NAMES:
        raise AgentConfigurationError(f"Unknown agent name: {agent_name}")
    values = os.environ if environment is None else environment
    prefix = normalized_agent.upper()

    provider = (
        _clean(values.get(f"{prefix}_PROVIDER")) or DEFAULT_PROVIDER
    ).casefold()
    if provider not in SUPPORTED_PROVIDERS:
        choices = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise AgentConfigurationError(
            f"{prefix}_PROVIDER must be one of: {choices}"
        )

    config = AgentProviderConfig(
        agent_name=normalized_agent,
        provider=provider,
        base_url=_clean(values.get(f"{prefix}_BASE_URL")),
        api_key_env=_clean(values.get(f"{prefix}_API_KEY_ENV"))
        or f"{prefix}_API_KEY",
        model=_clean(values.get(f"{prefix}_MODEL")),
        timeout_seconds=_float_value(
            f"{prefix}_TIMEOUT_SECONDS",
            values.get(f"{prefix}_TIMEOUT_SECONDS"),
            DEFAULT_TIMEOUT_SECONDS,
        ),
        max_retries=_int_value(
            f"{prefix}_MAX_RETRIES",
            values.get(f"{prefix}_MAX_RETRIES"),
            DEFAULT_MAX_RETRIES,
        ),
        organisation=_clean(values.get(f"{prefix}_ORGANISATION")),
        project=_clean(values.get(f"{prefix}_PROJECT")),
        prompt_template_version=_clean(
            values.get(f"{prefix}_PROMPT_TEMPLATE_VERSION")
        )
        or "v1",
    )
    return config


def load_agent_configs(
    environment: Mapping[str, str] | None = None,
) -> dict[str, AgentProviderConfig]:
    """Load every agent configuration; agents may use different providers."""

    return {
        name: load_agent_config(name, environment) for name in AGENT_NAMES
    }
