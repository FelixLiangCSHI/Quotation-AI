"""OpenAI-compatible chat-completions provider.

No vendor SDK is imported. Only the wire format is implemented, so any
OpenAI-compatible gateway can be configured through environment variables.
"""

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

SYSTEM_PROMPT = (
    "You assist an internal quotation workflow. Reply with a single JSON "
    "object matching the requested schema. Never invent prices, discounts, "
    "margins or approval decisions."
)


class OpenAICompatibleProvider:
    provider_name = "openai_compatible"

    def __init__(
        self,
        config: AgentProviderConfig,
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or UrllibTransport()

    def _headers(self) -> dict[str, str]:
        api_key = self._config.resolve_api_key()
        if not api_key:
            raise AgentProviderConfigurationError(
                "API key environment variable "
                f"{self._config.api_key_env} is not set."
            )
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + api_key,
        }
        if self._config.organisation:
            headers["OpenAI-Organization"] = self._config.organisation
        if self._config.project:
            headers["OpenAI-Project"] = self._config.project
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
        if not self._config.model:
            raise AgentProviderConfigurationError("Model is not configured.")
        headers = self._headers()
        started_at = utc_now()
        user_content = json.dumps(
            {
                "agent": context.agent_name,
                "task": task,
                "prompt_template_version": context.prompt_template_version,
                "response_schema": getattr(
                    response_schema, "__name__", "unknown"
                ),
                "input": input_payload,
            }
        )
        request_payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
        }

        last_error: Exception | None = None
        for _ in range(max(self._config.max_retries, 0) + 1):
            try:
                envelope = self._transport.post_json(
                    url=f"{base_url}/chat/completions",
                    payload=request_payload,
                    headers=headers,
                    timeout_seconds=context.timeout_seconds,
                )
                break
            except AgentProviderError as error:
                last_error = error
        else:
            raise last_error or AgentProviderError("Provider request failed.")

        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise AgentProviderError(
                "Provider response envelope is not OpenAI-compatible."
            ) from error
        if not isinstance(content, str):
            raise AgentProviderError("Provider message content must be text.")
        usage = envelope.get("usage")
        return AgentInvocationResult(
            raw_response=content,
            provider_name=self.provider_name,
            model=envelope.get("model") or self._config.model,
            usage=usage if isinstance(usage, dict) else {},
            started_at=started_at,
            ended_at=utc_now(),
        )

    def health_check(self) -> ProviderHealth:
        problems = []
        base_url = None
        try:
            base_url = validate_base_url(self._config.base_url)
        except AgentProviderConfigurationError as error:
            problems.append(str(error))
        if not self._config.model:
            problems.append("Model is not configured.")
        if self._config.resolve_api_key() is None:
            problems.append(
                f"API key environment variable {self._config.api_key_env} "
                "is not set."
            )
        configured = not problems
        return ProviderHealth(
            provider_name=self.provider_name,
            configured=configured,
            healthy=configured,
            detail=(
                "Endpoint, model and API key are configured."
                if configured
                else " ".join(problems)
            ),
            model=self._config.model,
            base_url=base_url,
        )
