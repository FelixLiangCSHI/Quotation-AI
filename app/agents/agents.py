"""Agent 1-4 interfaces.

Each agent owns:

* a request dataclass containing only what the agent is allowed to see,
* a deterministic baseline that is always available,
* a strict response schema,
* business rules and protected facts enforced by :mod:`app.agents.pipeline`.

AI output is never allowed to create or change a trusted price, rule outcome,
approval status or customer-safe boundary. Those values are copied from the
deterministic baseline by the code below, not from the provider response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from app.agents.audit import AgentAuditLog
from app.agents.config import AgentProviderConfig, load_agent_config
from app.agents.contracts import AgentProvider, ProviderHealth
from app.agents.pipeline import AgentOutcome, run_agent_task
from app.agents.providers import build_provider
from app.agents.schemas import (
    Agent1RequirementResponse,
    Agent2PricingResponse,
    Agent3EmailResponse,
    Agent4DocumentPlanResponse,
    DocumentSectionPlan,
    ExtractedRequirement,
)
from app.config import CUSTOMER_PROHIBITED_FIELDS


class BaseAgent:
    agent_name = "agent"

    def __init__(
        self,
        *,
        config: AgentProviderConfig | None = None,
        provider: AgentProvider | None = None,
        audit_log: AgentAuditLog | None = None,
    ) -> None:
        self.config = config or load_agent_config(self.agent_name)
        if self.config.agent_name != self.agent_name:
            raise ValueError(
                f"Configuration for {self.config.agent_name} cannot be used "
                f"by {self.agent_name}."
            )
        self.provider = provider if provider is not None else build_provider(self.config)
        self.audit_log = audit_log or AgentAuditLog()

    def health_check(self) -> ProviderHealth:
        """Secret-free provider readiness snapshot."""

        return self.provider.health_check()


# --- Agent 1 ---------------------------------------------------------------


@dataclass(frozen=True)
class RequirementRequest:
    customer_request: str
    known_fields: dict[str, str] = field(default_factory=dict)
    missing_fields: tuple[str, ...] = ()
    candidate_products: tuple[str, ...] = ()


class Agent1RequirementAgent(BaseAgent):
    """Requirement extraction, request interpretation and rationale."""

    agent_name = "agent1"
    task = "extract_requirements"

    def run(self, request: RequirementRequest) -> AgentOutcome[Agent1RequirementResponse]:
        baseline = self.deterministic_baseline(request)
        payload = {
            "customer_request": request.customer_request,
            "known_fields": dict(request.known_fields),
            "missing_fields": list(request.missing_fields),
            "candidate_products": list(request.candidate_products),
            "deterministic_baseline": baseline.model_dump(),
        }
        return run_agent_task(
            task=self.task,
            config=self.config,
            provider=self.provider,
            input_payload=payload,
            response_schema=Agent1RequirementResponse,
            deterministic_factory=lambda: baseline,
            business_rules=(_no_commercial_claims,),
            audit_log=self.audit_log,
        )

    @staticmethod
    def deterministic_baseline(
        request: RequirementRequest,
    ) -> Agent1RequirementResponse:
        requirements = [
            ExtractedRequirement(field_name=name, value=value, confidence=1.0)
            for name, value in sorted(request.known_fields.items())
            if value
        ]
        questions = [
            f"Please provide the {name.replace('_', ' ')}."
            for name in request.missing_fields
        ]
        rationale = (
            "Deterministic recommendation based on the confirmed requirement "
            "fields and the configured product rules."
        )
        return Agent1RequirementResponse(
            requirements=requirements,
            product_interpretation=request.customer_request[:1000],
            missing_questions=questions,
            recommendation_rationale=rationale,
        )


# --- Agent 2 ---------------------------------------------------------------


@dataclass(frozen=True)
class PricingNarrativeRequest:
    evidence_lines: tuple[str, ...] = ()
    analysis_lines: tuple[str, ...] = ()
    risk_lines: tuple[str, ...] = ()
    protected_values: tuple[str, ...] = ()


class Agent2PricingNarrativeAgent(BaseAgent):
    """Explains deterministic pricing output. Never calculates prices."""

    agent_name = "agent2"
    task = "summarise_pricing_evidence"

    def run(
        self, request: PricingNarrativeRequest
    ) -> AgentOutcome[Agent2PricingResponse]:
        baseline = self.deterministic_baseline(request)
        payload = {
            "evidence_lines": list(request.evidence_lines),
            "analysis_lines": list(request.analysis_lines),
            "risk_lines": list(request.risk_lines),
            "deterministic_baseline": baseline.model_dump(),
        }
        return run_agent_task(
            task=self.task,
            config=self.config,
            provider=self.provider,
            input_payload=payload,
            response_schema=Agent2PricingResponse,
            deterministic_factory=lambda: baseline,
            protected_values=request.protected_values,
            business_rules=(_no_commercial_claims,),
            audit_log=self.audit_log,
        )

    @staticmethod
    def deterministic_baseline(
        request: PricingNarrativeRequest,
    ) -> Agent2PricingResponse:
        return Agent2PricingResponse(
            evidence_summary="\n".join(request.evidence_lines),
            analysis_explanation="\n".join(request.analysis_lines),
            risks=list(request.risk_lines),
        )


# --- Agent 3 ---------------------------------------------------------------


@dataclass(frozen=True)
class EmailWordingRequest:
    email_type: str
    subject: str
    body: str
    protected_values: tuple[str, ...] = ()


class Agent3EmailWordingAgent(BaseAgent):
    """Rewrites internal and customer email wording only."""

    agent_name = "agent3"
    task = "rewrite_email"

    def run(self, request: EmailWordingRequest) -> AgentOutcome[Agent3EmailResponse]:
        baseline = self.deterministic_baseline(request)
        payload = {
            "email_type": request.email_type,
            "subject": request.subject,
            "body": request.body,
            "deterministic_baseline": baseline.model_dump(),
        }

        def _email_type_unchanged(
            candidate: Agent3EmailResponse, _payload: dict
        ) -> Sequence[str]:
            if candidate.email_type != request.email_type:
                return ("Email type must not be changed by the AI provider.",)
            return ()

        return run_agent_task(
            task=self.task,
            config=self.config,
            provider=self.provider,
            input_payload=payload,
            response_schema=Agent3EmailResponse,
            deterministic_factory=lambda: baseline,
            protected_values=request.protected_values,
            business_rules=(_email_type_unchanged,),
            audit_log=self.audit_log,
        )

    @staticmethod
    def deterministic_baseline(request: EmailWordingRequest) -> Agent3EmailResponse:
        return Agent3EmailResponse(
            email_type=request.email_type,
            subject=request.subject,
            body=request.body,
        )


# --- Agent 4 ---------------------------------------------------------------


@dataclass(frozen=True)
class DocumentPlanRequest:
    section_ids: tuple[str, ...]
    section_headings: dict[str, str] = field(default_factory=dict)
    customer_safe_facts: tuple[str, ...] = ()
    allowed_chart_ids: tuple[str, ...] = ()


class Agent4DocumentPlanAgent(BaseAgent):
    """Produces a DocumentPlan. Never produces prices or approval status."""

    agent_name = "agent4"
    task = "plan_document"

    def run(
        self, request: DocumentPlanRequest
    ) -> AgentOutcome[Agent4DocumentPlanResponse]:
        baseline = self.deterministic_baseline(request)
        allowed = set(request.section_ids)
        allowed_charts = set(request.allowed_chart_ids)
        payload = {
            "section_ids": list(request.section_ids),
            "section_headings": dict(request.section_headings),
            "allowed_chart_ids": list(request.allowed_chart_ids),
            "deterministic_baseline": baseline.model_dump(),
        }

        def _sections_are_known(
            candidate: Agent4DocumentPlanResponse, _payload: dict
        ) -> Sequence[str]:
            problems = []
            proposed = [section.section_id for section in candidate.sections]
            unknown = sorted(set(proposed) - allowed)
            if unknown:
                problems.append("Unknown document sections proposed.")
            if len(set(proposed)) != len(proposed):
                problems.append("Duplicate document sections proposed.")
            if set(proposed) != allowed:
                problems.append("Document plan must keep every required section.")
            if allowed_charts:
                unknown_charts = sorted(
                    {caption.chart_id for caption in candidate.chart_captions}
                    - allowed_charts
                )
                if unknown_charts:
                    problems.append("Unknown chart identifiers proposed.")
            return problems

        return run_agent_task(
            task=self.task,
            config=self.config,
            provider=self.provider,
            input_payload=payload,
            response_schema=Agent4DocumentPlanResponse,
            deterministic_factory=lambda: baseline,
            protected_values=request.customer_safe_facts,
            business_rules=(_sections_are_known, _no_internal_fields),
            audit_log=self.audit_log,
        )

    @staticmethod
    def deterministic_baseline(
        request: DocumentPlanRequest,
    ) -> Agent4DocumentPlanResponse:
        sections = [
            DocumentSectionPlan(
                section_id=section_id,
                heading=request.section_headings.get(
                    section_id, section_id.replace("_", " ").title()
                ),
                narrative="",
            )
            for section_id in request.section_ids
        ]
        return Agent4DocumentPlanResponse(
            sections=sections,
            customer_safe_summary="\n".join(request.customer_safe_facts),
        )


# --- shared business rules -------------------------------------------------

COMMERCIAL_CLAIM_TERMS = (
    "approved",
    "discount",
    "gross margin",
    "final price",
    "net price",
    "minimum price",
)


def _collect_text(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _collect_text(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _collect_text(item)]
    return []


def _agent_text(candidate) -> str:
    return " ".join(_collect_text(candidate.model_dump())).casefold()


def _no_commercial_claims(candidate, _payload: dict) -> Sequence[str]:
    """AI narrative must not assert new commercial or approval decisions."""

    text = _agent_text(candidate)
    return [
        f"AI output must not assert commercial decisions: {term}"
        for term in COMMERCIAL_CLAIM_TERMS
        if term in text
    ]


def _no_internal_fields(candidate, _payload: dict) -> Sequence[str]:
    """Customer-facing plans must not mention internal-only field names."""

    text = _agent_text(candidate)
    problems = []
    for name in sorted(CUSTOMER_PROHIBITED_FIELDS):
        phrase = name.replace("_", " ")
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            problems.append(
                f"Customer-safe output must not reference internal field: {name}"
            )
    return problems
