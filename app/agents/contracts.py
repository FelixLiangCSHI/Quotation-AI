"""Provider-neutral contracts for the Agent 1-4 infrastructure.

Nothing in this module imports a provider SDK. Providers are plugged in at the
edge and every provider result must pass through
:mod:`app.agents.pipeline` before it can influence the workflow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from app.quotation_models import utc_now


class ErrorCategory(str, Enum):
    """Normalised failure reasons recorded in the invocation audit."""

    NONE = "none"
    MISSING_CONFIGURATION = "missing_configuration"
    INVALID_CONFIGURATION = "invalid_configuration"
    TIMEOUT = "timeout"
    INVALID_JSON = "invalid_json"
    SCHEMA_VALIDATION = "schema_validation"
    BUSINESS_RULE = "business_rule"
    PROTECTED_FIELD = "protected_field"
    PROVIDER_ERROR = "provider_error"
    UNSAFE_OUTPUT = "unsafe_output"


class InvocationStatus(str, Enum):
    ACCEPTED = "accepted"
    FALLBACK = "fallback"


class AgentProviderError(RuntimeError):
    """Raised by providers. Always mapped to a deterministic fallback."""

    category = ErrorCategory.PROVIDER_ERROR


class AgentProviderTimeout(AgentProviderError):
    category = ErrorCategory.TIMEOUT


class AgentProviderConfigurationError(AgentProviderError):
    category = ErrorCategory.MISSING_CONFIGURATION


class AgentProviderUnsafeOutput(AgentProviderError):
    category = ErrorCategory.UNSAFE_OUTPUT


@dataclass(frozen=True)
class AgentInvocationContext:
    """Non-secret metadata passed to a provider invocation."""

    agent_name: str
    prompt_template_version: str
    timeout_seconds: float = 30.0
    max_retries: int = 0
    correlation_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentInvocationResult:
    """Raw provider output plus non-sensitive usage metadata."""

    raw_response: str
    provider_name: str
    model: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=utc_now)
    ended_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class ProviderHealth:
    """Health snapshot. Never contains a secret value."""

    provider_name: str
    configured: bool
    healthy: bool
    detail: str
    model: str | None = None
    base_url: str | None = None


@runtime_checkable
class AgentProvider(Protocol):
    provider_name: str

    def invoke(
        self,
        *,
        task: str,
        input_payload: dict,
        response_schema: type,
        context: AgentInvocationContext,
    ) -> AgentInvocationResult:
        """Return a raw provider response for ``task``."""

    def health_check(self) -> ProviderHealth:
        """Return a secret-free readiness snapshot."""
