"""Validation pipeline and circuit-breaker style fallback runtime.

Every provider result travels through:

    raw response
      -> JSON parsing
      -> schema validation
      -> business-rule validation
      -> protected-field validation
      -> accepted result or deterministic fallback
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Generic, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from app.agents.audit import AgentAuditLog, AgentInvocationAudit
from app.agents.config import AgentProviderConfig
from app.agents.contracts import (
    AgentInvocationContext,
    AgentProvider,
    AgentProviderConfigurationError,
    AgentProviderError,
    ErrorCategory,
    InvocationStatus,
)
from app.quotation_models import utc_now

SchemaT = TypeVar("SchemaT", bound=BaseModel)

BusinessRuleValidator = Callable[[BaseModel, dict], Sequence[str]]


class AgentOutputRejected(Exception):
    """Internal signal that an AI output must not be used."""

    def __init__(self, category: ErrorCategory, detail: str) -> None:
        super().__init__(detail)
        self.category = category
        self.detail = detail


@dataclass(frozen=True)
class AgentOutcome(Generic[SchemaT]):
    """Result of an agent call: always usable, deterministic when needed."""

    value: SchemaT
    audit: AgentInvocationAudit

    @property
    def fallback_used(self) -> bool:
        return self.audit.fallback_used


def parse_json(raw_response: str) -> dict:
    try:
        parsed = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError) as error:
        raise AgentOutputRejected(
            ErrorCategory.INVALID_JSON, "Provider output is not valid JSON."
        ) from error
    if not isinstance(parsed, dict):
        raise AgentOutputRejected(
            ErrorCategory.INVALID_JSON, "Provider output must be a JSON object."
        )
    return parsed


def validate_schema(payload: dict, response_schema: type[SchemaT]) -> SchemaT:
    try:
        return response_schema.model_validate(payload)
    except ValidationError as error:
        raise AgentOutputRejected(
            ErrorCategory.SCHEMA_VALIDATION,
            f"Schema validation failed with {error.error_count()} error(s).",
        ) from error


def validate_protected_values(
    candidate: BaseModel,
    protected_values: Sequence[str],
) -> None:
    """Reject output that drops a protected commercial fact."""

    if not protected_values:
        return
    serialised = json.dumps(candidate.model_dump(), ensure_ascii=False)
    missing = [
        value
        for value in protected_values
        if value and str(value) not in serialised
    ]
    if missing:
        raise AgentOutputRejected(
            ErrorCategory.PROTECTED_FIELD,
            f"{len(missing)} protected value(s) missing from AI output.",
        )


def validate_business_rules(
    candidate: BaseModel,
    input_payload: dict,
    validators: Sequence[BusinessRuleValidator],
) -> None:
    problems: list[str] = []
    for validator in validators:
        problems.extend(validator(candidate, input_payload))
    if problems:
        raise AgentOutputRejected(
            ErrorCategory.BUSINESS_RULE,
            f"{len(problems)} business-rule violation(s) in AI output.",
        )


def _error_category(error: Exception) -> ErrorCategory:
    category = getattr(error, "category", None)
    if isinstance(category, ErrorCategory):
        return category
    return ErrorCategory.PROVIDER_ERROR


def run_agent_task(
    *,
    task: str,
    config: AgentProviderConfig,
    provider: AgentProvider | None,
    input_payload: dict,
    response_schema: type[SchemaT],
    deterministic_factory: Callable[[], SchemaT],
    protected_values: Sequence[str] = (),
    business_rules: Sequence[BusinessRuleValidator] = (),
    audit_log: AgentAuditLog | None = None,
    correlation_id: str | None = None,
    metadata: Mapping[str, str] | None = None,
) -> AgentOutcome[SchemaT]:
    """Invoke ``provider`` and fall back to deterministic output on any failure."""

    started_at = utc_now()
    context = AgentInvocationContext(
        agent_name=config.agent_name,
        prompt_template_version=config.prompt_template_version,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
        correlation_id=correlation_id,
        metadata=dict(metadata or {}),
    )
    usage: dict[str, Any] = {}
    model = config.model
    error_category = ErrorCategory.NONE
    error_detail = ""
    value: SchemaT | None = None

    try:
        if provider is None:
            raise AgentProviderConfigurationError(
                "No provider is configured for this agent."
            )
        result = provider.invoke(
            task=task,
            input_payload=input_payload,
            response_schema=response_schema,
            context=context,
        )
        usage = dict(result.usage)
        model = result.model or model
        payload = parse_json(result.raw_response)
        candidate = validate_schema(payload, response_schema)
        validate_business_rules(candidate, input_payload, business_rules)
        validate_protected_values(candidate, protected_values)
        value = candidate
    except AgentOutputRejected as rejection:
        error_category = rejection.category
        error_detail = rejection.detail
    except AgentProviderError as error:
        error_category = _error_category(error)
        error_detail = str(error)
    except Exception as error:  # noqa: BLE001 - fail closed, never block workflow
        error_category = ErrorCategory.PROVIDER_ERROR
        error_detail = type(error).__name__

    fallback_used = value is None
    if fallback_used:
        value = deterministic_factory()

    audit = AgentInvocationAudit(
        agent_name=config.agent_name,
        provider=config.provider,
        model=model,
        started_at=started_at,
        ended_at=utc_now(),
        status=(
            InvocationStatus.FALLBACK if fallback_used else InvocationStatus.ACCEPTED
        ),
        fallback_used=fallback_used,
        prompt_template_version=config.prompt_template_version,
        error_category=error_category,
        error_detail=error_detail,
        usage=usage,
    )
    if audit_log is not None:
        audit_log.record(audit)
    return AgentOutcome(value=value, audit=audit)
