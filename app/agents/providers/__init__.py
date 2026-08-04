"""Provider implementations and the provider factory."""

from __future__ import annotations

from typing import Callable, Mapping

from app.agents.config import AgentProviderConfig
from app.agents.contracts import AgentProvider
from app.agents.providers.deterministic import DeterministicProvider
from app.agents.providers.http_json import HttpJsonProvider
from app.agents.providers.mock import MockProvider
from app.agents.providers.openai_compatible import OpenAICompatibleProvider
from app.agents.providers.transport import HttpTransport, UrllibTransport

__all__ = [
    "DeterministicProvider",
    "HttpJsonProvider",
    "HttpTransport",
    "MockProvider",
    "OpenAICompatibleProvider",
    "UrllibTransport",
    "build_provider",
]


def build_provider(
    config: AgentProviderConfig,
    *,
    transport: HttpTransport | None = None,
    overrides: Mapping[str, Callable[[AgentProviderConfig], AgentProvider]]
    | None = None,
) -> AgentProvider:
    """Create the provider selected by ``config.provider``."""

    if overrides and config.provider in overrides:
        return overrides[config.provider](config)
    if config.provider == "deterministic":
        return DeterministicProvider()
    if config.provider == "mock":
        return MockProvider()
    if config.provider == "http_json":
        return HttpJsonProvider(config, transport=transport)
    if config.provider == "openai_compatible":
        return OpenAICompatibleProvider(config, transport=transport)
    raise ValueError(f"Unsupported provider: {config.provider}")
