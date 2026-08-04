"""Generic HTTP JSON provider."""

from __future__ import annotations

import json
from typing import Any

from app.agents.config import AgentProviderConfig
from app.agents.contracts import (
    AgentInvocationContext,
    AgentInvocationResult,
    AgentProviderConfigurationError,
    AgentProviderError,
    ProviderHealth,
)
from app.agents.providers.transport import (
    HttpTransport,
    UrllibTransport,
    validate_base_url,
)
from app.quotation_models import utc_now


class HttpJsonProvider:
    """Posts the task payload to a configured JSON endpoint."""

    provider_name = "http_json"

    def __init__(
        self,
        config: AgentProviderConfig,
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibTransport()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        api_key = self._config.resolve_api_key()
        if api_key:
            # Resolved at call time and never stored, logged or audited.
            headers["Authorization"] = "Bearer " + api_key
        return headers

    def invoke(
        self,
        *,
        task: str,
        input_payload: dict,
        response_schema: type,
        context: AgentInvocationContext,
    ) -> AgentInvocationResult:
        base_url = validate_base_url(self._config.base_url)
        started_at = utc_now()
        request_payload: dict[str, Any] = {
            "agent": context.agent_name,
            "task": task,
            "prompt_template_version": context.prompt_template_version,
            "response_schema": getattr(response_schema, "__name__", "unknown"),
            "input": input_payload,
        }
        if self._config.model:
            request_payload["model"] = self._config.model

        last_error: Exception | None = None
        for _ in range(max(self._config.max_retries, 0) + 1):
            try:
                envelope = self._transport.post_json(
                    url=f"{base_url}/{task}",
                    payload=request_payload,
                    headers=self._headers(),
                    timeout_seconds=context.timeout_seconds,
                )
                break
            except AgentProviderError as error:
                last_error = error
        else:
            raise last_error or AgentProviderError("Provider request failed.")

        content = envelope.get("output", envelope.get("result"))
        if content is None:
            raise AgentProviderError("Provider response has no output field.")
        raw = content if isinstance(content, str) else json.dumps(content)
        usage = envelope.get("usage")
        return AgentInvocationResult(
            raw_response=raw,
            provider_name=self.provider_name,
            model=self._config.model,
            usage=usage if isinstance(usage, dict) else {},
            started_at=started_at,
            ended_at=utc_now(),
        )

    def health_check(self) -> ProviderHealth:
        try:
            base_url = validate_base_url(self._config.base_url)
        except AgentProviderConfigurationError as error:
            return ProviderHealth(
                provider_name=self.provider_name,
                configured=False,
                healthy=False,
                detail=str(error),
                model=self._config.model,
            )
        has_key = self._config.resolve_api_key() is not None
        return ProviderHealth(
            provider_name=self.provider_name,
            configured=True,
            healthy=True,
            detail=(
                "Endpoint configured; API key present."
                if has_key
                else "Endpoint configured; no API key set (unauthenticated)."
            ),
            model=self._config.model,
            base_url=base_url,
        )
