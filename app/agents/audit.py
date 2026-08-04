"""Invocation audit records for agent calls.

Records contain non-sensitive metadata only: no API keys, no prompts and no
raw business payloads unless explicitly enabled by the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from app.agents.contracts import ErrorCategory, InvocationStatus

LOGGER = logging.getLogger(__name__)

SECRET_HINTS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "authorization",
    "credential",
    "bearer",
)


def _is_secret_like(name: str) -> bool:
    lowered = name.casefold()
    return any(hint in lowered for hint in SECRET_HINTS)


def scrub_usage(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """Drop any key that looks like a credential."""

    if not usage:
        return {}
    return {
        key: value
        for key, value in usage.items()
        if not _is_secret_like(str(key))
    }


@dataclass(frozen=True)
class AgentInvocationAudit:
    agent_name: str
    provider: str
    model: str | None
    started_at: datetime
    ended_at: datetime
    status: InvocationStatus
    fallback_used: bool
    prompt_template_version: str
    error_category: ErrorCategory = ErrorCategory.NONE
    error_detail: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return max((self.ended_at - self.started_at).total_seconds(), 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "provider": self.provider,
            "model": self.model,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "status": self.status.value,
            "fallback_used": self.fallback_used,
            "prompt_template_version": self.prompt_template_version,
            "error_category": self.error_category.value,
            "error_detail": self.error_detail,
            "usage": scrub_usage(self.usage),
        }


class AgentAuditLog:
    """In-memory audit sink used by the agent runtime."""

    def __init__(self) -> None:
        self._records: list[AgentInvocationAudit] = []

    def record(self, audit: AgentInvocationAudit) -> AgentInvocationAudit:
        self._records.append(audit)
        LOGGER.info("agent invocation: %s", audit.to_dict())
        return audit

    @property
    def records(self) -> tuple[AgentInvocationAudit, ...]:
        return tuple(self._records)

    def clear(self) -> None:
        self._records.clear()
