"""Mock provider used by tests and local demos. Performs no network I/O."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from app.agents.contracts import (
    AgentInvocationContext,
    AgentInvocationResult,
    AgentProviderError,
    ProviderHealth,
)
from app.quotation_models import utc_now

ResponseFactory = Callable[[str, dict], str]


class MockProvider:
    """Returns canned raw responses keyed by task name.

    ``responses`` may map a task to a string (returned verbatim), to a dict
    (serialised to JSON) or to an exception instance (raised) so tests can
    force any failure branch without touching the network.
    """

    provider_name = "mock"

    def __init__(
        self,
        responses: Mapping[str, Any] | None = None,
        *,
        default: Any = None,
        model: str | None = "mock-model",
        usage: Mapping[str, Any] | None = None,
        healthy: bool = True,
    ) -> None:
        self._responses = dict(responses or {})
        self._default = default
        self._model = model
        self._usage = dict(usage or {"prompt_tokens": 0, "completion_tokens": 0})
        self._healthy = healthy
        self.calls: list[tuple[str, dict]] = []

    def invoke(
        self,
        *,
        task: str,
        input_payload: dict,
        response_schema: type,
        context: AgentInvocationContext,
    ) -> AgentInvocationResult:
        started_at = utc_now()
        self.calls.append((task, dict(input_payload)))
        response = self._responses.get(task, self._default)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response(task, input_payload)
        if response is None:
            raise AgentProviderError(f"No mock response configured for {task}")
        raw = response if isinstance(response, str) else json.dumps(response)
        return AgentInvocationResult(
            raw_response=raw,
            provider_name=self.provider_name,
            model=self._model,
            usage=dict(self._usage),
            started_at=started_at,
            ended_at=utc_now(),
        )

    def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.provider_name,
            configured=True,
            healthy=self._healthy,
            detail="Mock provider ready." if self._healthy else "Mock provider disabled.",
            model=self._model,
        )
