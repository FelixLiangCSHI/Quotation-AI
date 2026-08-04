"""Deterministic provider: the always-available, offline default."""

from __future__ import annotations

import json
from typing import Callable

from app.agents.contracts import (
    AgentInvocationContext,
    AgentInvocationResult,
    ProviderHealth,
)
from app.quotation_models import utc_now


class DeterministicProvider:
    """Serialises the deterministic baseline supplied by the caller.

    The baseline is produced by existing deterministic code, so this provider
    never introduces new commercial facts and never performs any I/O.
    """

    provider_name = "deterministic"

    def __init__(
        self,
        baseline_factory: Callable[[str, dict], dict] | None = None,
    ) -> None:
        self._baseline_factory = baseline_factory

    def invoke(
        self,
        *,
        task: str,
        input_payload: dict,
        response_schema: type,
        context: AgentInvocationContext,
    ) -> AgentInvocationResult:
        started_at = utc_now()
        if self._baseline_factory is not None:
            payload = self._baseline_factory(task, input_payload)
        else:
            payload = dict(input_payload.get("deterministic_baseline") or {})
        return AgentInvocationResult(
            raw_response=json.dumps(payload),
            provider_name=self.provider_name,
            model=None,
            usage={"mode": "deterministic"},
            started_at=started_at,
            ended_at=utc_now(),
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.provider_name,
            configured=True,
            healthy=True,
            detail="Deterministic provider requires no external configuration.",
        )
