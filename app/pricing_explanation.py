"""Agent 2 explanation of the deterministic pricing and margin result.

Agent 2 is optional. It may only *explain* a result that has already been
calculated deterministically. It can never change a cost, price, margin, rule
evaluation, policy threshold, decision status or approval requirement: those
values are copied from the trusted result by the code below.

Every candidate explanation is validated against the protected facts
(quotation ID, total revenue, total cost, gross margin, threshold, decision
status and currency). If the provider contradicts a trusted fact, times out or
fails in any other way, its output is discarded and the deterministic
explanation is used instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from app.agents.audit import AgentAuditLog
from app.agents.config import AgentProviderConfig, load_agent_config
from app.agents.contracts import AgentProvider
from app.agents.pipeline import run_agent_task
from app.agents.schemas import Agent2PricingResponse
from app.quotation_models import (
    AgentExplanation,
    CombinedDecision,
    QuotationPricingAnalysis,
)
from app.quotation_pricing import display_money, display_percent

AI_EXPLANATION_LABEL = (
    "AI-generated explanation — not part of the commercial decision."
)

_TOP_CONTRIBUTOR_COUNT = 3


@dataclass(frozen=True)
class ProtectedFacts:
    """The trusted values an explanation is not allowed to contradict."""

    quotation_id: str
    currency: str
    total_revenue: str | None
    total_cost: str | None
    gross_margin_amount: str | None
    gross_margin_percent: str | None
    threshold_percent: str | None
    decision_status: str

    def as_values(self) -> tuple[str, ...]:
        return tuple(
            value
            for value in (
                self.quotation_id,
                self.currency,
                display_money(self.total_revenue),
                display_money(self.total_cost),
                display_money(self.gross_margin_amount),
                display_percent(self.gross_margin_percent),
                self.threshold_percent,
                self.decision_status.replace("_", " ").upper(),
            )
            if value
        )


def protected_facts(
    pricing: QuotationPricingAnalysis,
    decision: CombinedDecision,
) -> ProtectedFacts:
    return ProtectedFacts(
        quotation_id=pricing.quotation_id,
        currency=pricing.currency,
        total_revenue=pricing.total_revenue,
        total_cost=pricing.total_cost,
        gross_margin_amount=pricing.gross_margin_amount,
        gross_margin_percent=pricing.gross_margin_percent,
        threshold_percent=decision.threshold_percent,
        decision_status=decision.status,
    )


def deterministic_explanation(
    pricing: QuotationPricingAnalysis,
    decision: CombinedDecision,
) -> AgentExplanation:
    """The always-available explanation. Uses only trusted values."""

    facts = protected_facts(pricing, decision)
    summary_lines = [
        f"Quotation {facts.quotation_id} totals "
        f"{display_money(facts.total_revenue)} {facts.currency} in revenue "
        f"against {display_money(facts.total_cost)} {facts.currency} of "
        "estimated cost."
    ]
    if facts.gross_margin_percent is not None:
        summary_lines.append(
            f"Quotation gross margin is "
            f"{display_money(facts.gross_margin_amount)} {facts.currency} "
            f"({display_percent(facts.gross_margin_percent)}%)."
        )
    else:
        summary_lines.append(
            "A trustworthy quotation gross margin could not be calculated."
        )

    revenue_contributors = _top_lines(pricing, key="line_revenue")
    cost_contributors = _top_lines(pricing, key="total_cost")
    if revenue_contributors:
        summary_lines.append(
            "Largest revenue contributors: " + ", ".join(revenue_contributors) + "."
        )
    if cost_contributors:
        summary_lines.append(
            "Largest cost contributors: " + ", ".join(cost_contributors) + "."
        )

    explanation_lines = [_decision_sentence(decision, facts)]
    if decision.blocking_reasons:
        explanation_lines.append(
            "Blocking reasons: "
            + ", ".join(reason.replace("_", " ") for reason in decision.blocking_reasons)
            + "."
        )
    if decision.review_reasons:
        explanation_lines.extend(decision.review_reasons)
    if decision.triggered_rule_ids:
        explanation_lines.append(
            "Triggered rules: " + ", ".join(decision.triggered_rule_ids) + "."
        )
    explanation_lines.append(
        "Approver summary: "
        + _approver_summary(decision)
    )

    risks = [
        f"Missing data: {flag.replace('_', ' ')}"
        for flag in pricing.missing_data_flags
    ]
    risks.extend(pricing.warnings)

    return AgentExplanation(
        label=AI_EXPLANATION_LABEL,
        summary="\n".join(summary_lines),
        explanation="\n".join(explanation_lines),
        risks=risks,
        ai_generated=False,
        fallback_used=True,
        fallback_reason="deterministic_explanation",
    )


def explain_pricing_decision(
    pricing: QuotationPricingAnalysis,
    decision: CombinedDecision,
    *,
    provider: AgentProvider | None = None,
    config: AgentProviderConfig | None = None,
    audit_log: AgentAuditLog | None = None,
) -> AgentExplanation:
    """Return an explanation. Agent 2 is optional and never authoritative."""

    baseline = deterministic_explanation(pricing, decision)
    if provider is None:
        return baseline

    facts = protected_facts(pricing, decision)
    agent_config = config or load_agent_config("agent2")
    baseline_response = Agent2PricingResponse(
        evidence_summary=baseline.summary,
        analysis_explanation=baseline.explanation,
        risks=list(baseline.risks),
    )
    payload = {
        "trusted_result": {
            "quotation_id": facts.quotation_id,
            "currency": facts.currency,
            "total_revenue": display_money(facts.total_revenue),
            "total_cost": display_money(facts.total_cost),
            "gross_margin_amount": display_money(facts.gross_margin_amount),
            "gross_margin_percent": display_percent(facts.gross_margin_percent),
            "threshold_percent": facts.threshold_percent,
            "decision_status": facts.decision_status,
            "policy_version_id": decision.policy_version_id,
            "triggered_rule_ids": list(decision.triggered_rule_ids),
        },
        "instruction": (
            "Explain the supplied trusted result in plain language. Do not "
            "change any number, threshold or status."
        ),
        "deterministic_baseline": baseline_response.model_dump(),
    }

    outcome = run_agent_task(
        task="explain_quotation_margin",
        config=agent_config,
        provider=provider,
        input_payload=payload,
        response_schema=Agent2PricingResponse,
        deterministic_factory=lambda: baseline_response,
        protected_values=facts.as_values(),
        business_rules=(_contradiction_check(facts),),
        audit_log=audit_log,
    )
    if outcome.fallback_used:
        return AgentExplanation(
            label=AI_EXPLANATION_LABEL,
            summary=baseline.summary,
            explanation=baseline.explanation,
            risks=list(baseline.risks),
            ai_generated=False,
            fallback_used=True,
            fallback_reason=outcome.audit.error_category.value,
        )
    return AgentExplanation(
        label=AI_EXPLANATION_LABEL,
        summary=outcome.value.evidence_summary,
        explanation=outcome.value.analysis_explanation,
        risks=list(outcome.value.risks),
        ai_generated=True,
        fallback_used=False,
        fallback_reason="",
    )


def _contradiction_check(facts: ProtectedFacts):
    """Reject an explanation that states a different status or threshold."""

    allowed_status = facts.decision_status.replace("_", " ").casefold()
    other_statuses = {
        "pass",
        "review required",
        "blocked",
    } - {allowed_status}
    numeric_facts = {
        value
        for value in (
            display_money(facts.total_revenue),
            display_money(facts.total_cost),
            display_money(facts.gross_margin_amount),
            display_percent(facts.gross_margin_percent),
            facts.threshold_percent,
        )
        if value
    }
    numeric_decimals = {Decimal(value) for value in numeric_facts}

    def _validator(candidate, _payload: dict) -> Sequence[str]:
        text = " ".join(
            part
            for part in (
                candidate.evidence_summary,
                candidate.analysis_explanation,
                *candidate.risks,
            )
            if part
        )
        lowered = text.casefold()
        problems: list[str] = []
        for status in other_statuses:
            if re.search(rf"\b{re.escape(status)}\b", lowered):
                problems.append(
                    "AI output states a decision status that contradicts the "
                    "trusted result."
                )
                break
        for token in re.findall(r"\d+(?:\.\d+)?%", text):
            value = Decimal(token.rstrip("%"))
            if value not in numeric_decimals:
                problems.append(
                    "AI output states a percentage that is not a trusted value."
                )
                break
        return problems

    return _validator


def _decision_sentence(
    decision: CombinedDecision,
    facts: ProtectedFacts,
) -> str:
    if decision.status == "blocked":
        return (
            "The deterministic result is BLOCKED, so the quotation cannot be "
            "approved until it is corrected and pricing and validation are "
            "rerun."
        )
    margin = display_percent(facts.gross_margin_percent)
    if decision.status == "pass":
        return (
            f"The deterministic result is PASS because the quotation gross "
            f"margin of {margin}% is greater than the active threshold of "
            f"{facts.threshold_percent}%."
        )
    return (
        f"The deterministic result is REVIEW REQUIRED because the quotation "
        f"gross margin of {margin}% is not greater than the active threshold "
        f"of {facts.threshold_percent}%."
    )


def _approver_summary(decision: CombinedDecision) -> str:
    if decision.status == "pass":
        return (
            "The quotation cleared the provisional margin gate and still needs "
            "an authorised human confirmation before anything is sent."
        )
    if decision.status == "review_required":
        return (
            "A human approver must decide whether to accept this quotation at "
            "the calculated margin."
        )
    return "No approval is possible until the blocking issues are corrected."


def _top_lines(pricing: QuotationPricingAnalysis, *, key: str) -> list[str]:
    entries = [
        (Decimal(getattr(line, key)), line)
        for line in pricing.line_analyses
        if getattr(line, key) is not None
    ]
    entries.sort(key=lambda item: item[0], reverse=True)
    return [
        f"{line.product_id or line.line_id} ({display_money(str(amount))} "
        f"{pricing.currency})"
        for amount, line in entries[:_TOP_CONTRIBUTOR_COUNT]
        if amount > 0
    ]
