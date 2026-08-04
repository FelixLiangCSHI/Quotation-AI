"""Provider-neutral AI agent infrastructure (Agents 1-4).

The application runs fully deterministically without any API key. Providers
are optional, configured per agent, and any provider failure falls back to
deterministic output.
"""

from __future__ import annotations

from app.agents.agents import (
    Agent1RequirementAgent,
    Agent2PricingNarrativeAgent,
    Agent3EmailWordingAgent,
    Agent4DocumentPlanAgent,
    DocumentPlanRequest,
    EmailWordingRequest,
    PricingNarrativeRequest,
    RequirementRequest,
)
from app.agents.audit import AgentAuditLog, AgentInvocationAudit
from app.agents.config import (
    AGENT_NAMES,
    SUPPORTED_PROVIDERS,
    AgentConfigurationError,
    AgentProviderConfig,
    load_agent_config,
    load_agent_configs,
)
from app.agents.contracts import (
    AgentInvocationContext,
    AgentInvocationResult,
    AgentProvider,
    ErrorCategory,
    InvocationStatus,
    ProviderHealth,
)
from app.agents.health import agent_health_report
from app.agents.pipeline import AgentOutcome, run_agent_task
from app.agents.providers import (
    DeterministicProvider,
    HttpJsonProvider,
    MockProvider,
    OpenAICompatibleProvider,
    build_provider,
)
from app.agents.schemas import (
    Agent1RequirementResponse,
    Agent2PricingResponse,
    Agent3EmailResponse,
    Agent4DocumentPlanResponse,
)

__all__ = [
    "AGENT_NAMES",
    "SUPPORTED_PROVIDERS",
    "Agent1RequirementAgent",
    "Agent1RequirementResponse",
    "Agent2PricingNarrativeAgent",
    "Agent2PricingResponse",
    "Agent3EmailResponse",
    "Agent3EmailWordingAgent",
    "Agent4DocumentPlanAgent",
    "Agent4DocumentPlanResponse",
    "AgentAuditLog",
    "AgentConfigurationError",
    "AgentInvocationAudit",
    "AgentInvocationContext",
    "AgentInvocationResult",
    "AgentOutcome",
    "AgentProvider",
    "AgentProviderConfig",
    "DeterministicProvider",
    "DocumentPlanRequest",
    "EmailWordingRequest",
    "ErrorCategory",
    "HttpJsonProvider",
    "InvocationStatus",
    "MockProvider",
    "OpenAICompatibleProvider",
    "PricingNarrativeRequest",
    "ProviderHealth",
    "RequirementRequest",
    "agent_health_report",
    "build_provider",
    "load_agent_config",
    "load_agent_configs",
    "run_agent_task",
]
