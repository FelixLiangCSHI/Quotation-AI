from __future__ import annotations

from dataclasses import replace

from app.approval_workflow import prepare_approval
from app.commercial_policy import (
    CommercialPolicyVersion,
    active_commercial_policy,
)
from app.commercial_validation import (
    combine_validation_decision,
    validate_commercial,
)
from app.conversation_agent import (
    ConversationTurnResult,
    RequirementConversationAgent,
)
from app.margin_gate import evaluate_commercial_decision
from app.pricing_engine import PricingEngine
from app.pricing_explanation import explain_pricing_decision
from app.quotation_models import (
    ApprovalStatus,
    CombinedDecision,
    PricingResult,
    QuotationPricingAnalysis,
    QuotationWorkflowState,
    WorkflowStage,
)
from app.quotation_pricing import analyse_quotation_pricing
from app.recommender import QuoteRecommendation, RecommendationItem
from app.requirement_intake import confirm_pending
from app.rule_engine import QuotationRuleEngine
from app.technical_validation import validate_technical_configuration
from app.workflow_state import append_audit_event
from app.workflow_validation import invalidate_validation_outputs


class WorkflowOrchestrationError(ValueError):
    pass


def process_requirement_message(
    state: QuotationWorkflowState,
    message: str,
    agent: RequirementConversationAgent,
) -> ConversationTurnResult:
    result = agent.process_message(message, state.draft)
    before_approval_state = state.approval.status.value
    state.draft = result.updated_draft
    state.product_recommendation = result.product_recommendation
    state.current_stage = state.draft.status
    result = _auto_apply_confident_selection(state, result, agent)
    if result.changed_fields:
        invalidate_validation_outputs(state, clear_pricing=True)
        append_audit_event(
            state,
            "field_updated",
            actor="user",
            before_state=before_approval_state,
            after_state=state.approval.status.value,
            changed_fields=list(result.changed_fields),
            details={"changed_fields": list(result.changed_fields)},
        )
    return result


def _auto_apply_confident_selection(
    state: QuotationWorkflowState,
    result: ConversationTurnResult,
    agent: RequirementConversationAgent,
) -> ConversationTurnResult:
    """Apply a confident选型 without asking; only confirm when unsure.

    The user is asked to confirm a product only when the deterministic
    recommendation is below the confidence threshold, has a blocking rule
    issue, or does not exist. A confident, rule-clean proposal is applied
    directly and remains fully reversible from the product selector.
    """

    recommendation = result.product_recommendation
    if recommendation is None or recommendation.requires_confirmation:
        return result
    main_model = recommendation.main_model
    if main_model is None or state.draft.selected_product_ids:
        return result

    try:
        state.draft = agent.select_product(
            state.draft, main_model.product_id, recommendation
        )
    except ValueError:
        return result
    state.current_stage = state.draft.status
    notice = (
        f"Selected {main_model.short_description} "
        f"({main_model.product_id}) automatically because the选型 confidence "
        f"is {main_model.confidence:.0%}. You can change it at any time."
    )
    return replace(
        result,
        updated_draft=state.draft,
        changed_fields=tuple(
            dict.fromkeys((*result.changed_fields, "selected_product_ids"))
        ),
        missing_fields=tuple(state.draft.missing_fields),
        notices=(*result.notices, notice),
        ready_for_analysis=state.draft.status == WorkflowStage.READY_FOR_ANALYSIS,
        next_question=(
            None
            if state.draft.status == WorkflowStage.READY_FOR_ANALYSIS
            else result.next_question
        ),
    )


def apply_structured_requirements(
    state: QuotationWorkflowState,
    values: dict,
    agent: RequirementConversationAgent,
):
    """Apply a structured form submission to the same quotation state.

    The form and the conversation both funnel through the requirement merge
    logic, so both entry modes update the domain model identically.
    """

    before_approval_state = state.approval.status.value
    outcome = agent.apply_structured_form(state.draft, values)
    state.draft = outcome.draft
    state.current_stage = state.draft.status
    if outcome.changed_fields:
        invalidate_validation_outputs(state, clear_pricing=True)
        append_audit_event(
            state,
            "requirements_form_submitted",
            actor="user",
            before_state=before_approval_state,
            after_state=state.approval.status.value,
            changed_fields=list(outcome.changed_fields),
            details={
                "changed_fields": list(outcome.changed_fields),
                "rejected_fields": [
                    item.field_name for item in outcome.rejected
                ],
            },
        )
    return outcome


def confirm_requirement_candidate(
    state: QuotationWorkflowState,
    field_name: str,
    *,
    accept: bool = True,
):
    """Confirm or discard a low-confidence Agent 1 candidate."""

    before_approval_state = state.approval.status.value
    outcome = confirm_pending(state.draft, field_name, accept=accept)
    state.draft = outcome.draft
    if outcome.changed_fields:
        invalidate_validation_outputs(state, clear_pricing=True)
        append_audit_event(
            state,
            "requirement_confirmed",
            actor="user",
            before_state=before_approval_state,
            after_state=state.approval.status.value,
            changed_fields=list(outcome.changed_fields),
            details={"field_name": field_name, "accepted": accept},
        )
    return outcome


def select_recommended_product(
    state: QuotationWorkflowState,
    product_id: str,
    recommendation: QuoteRecommendation,
    agent: RequirementConversationAgent,
) -> None:
    before_approval_state = state.approval.status.value
    state.draft = agent.select_product(state.draft, product_id, recommendation)
    state.current_stage = state.draft.status
    invalidate_validation_outputs(state, clear_pricing=True)
    append_audit_event(
        state,
        "product_selected",
        actor="user",
        before_state=before_approval_state,
        after_state=state.approval.status.value,
        changed_fields=["selected_product_ids"],
        details={"product_id": product_id},
    )


def analyse_workflow_pricing(
    state: QuotationWorkflowState,
    pricing_engine: PricingEngine,
    recommendation: QuoteRecommendation | None,
) -> PricingResult:
    if not state.draft.selected_product_ids:
        raise WorkflowOrchestrationError(
            "Select a product before running pricing analysis."
        )
    selected_item = selected_recommendation_item(
        state.draft.selected_product_ids[0],
        recommendation,
    )
    result = pricing_engine.analyse(
        state.draft,
        product_description=(
            selected_item.short_description if selected_item else ""
        ),
    )
    state.pricing_result = result
    state.current_stage = WorkflowStage.ANALYSED
    state.validation_stale = True
    state.technical_validation = None
    state.commercial_validation = None
    state.combined_decision = None
    append_audit_event(
        state,
        "pricing_completed",
        actor="system",
        before_state=state.approval.status.value,
        after_state=state.approval.status.value,
        changed_fields=["pricing_result"],
        details={
            "product_id": state.draft.selected_product_ids[0],
            "pricing_available": result.recommended_unit_price is not None,
        },
    )
    return result


def validate_workflow(
    state: QuotationWorkflowState,
    recommendation: QuoteRecommendation | None,
    technical_engine: QuotationRuleEngine,
) -> CombinedDecision:
    if state.pricing_result is None:
        raise WorkflowOrchestrationError(
            "Run pricing analysis before validation."
        )
    technical = validate_technical_configuration(
        state.draft,
        recommendation,
        technical_engine,
    )
    commercial = validate_commercial(state.draft, state.pricing_result)
    decision = combine_validation_decision(technical, commercial)
    state.technical_validation = technical
    state.commercial_validation = commercial
    state.combined_decision = decision
    state.validation_stale = False
    approval = prepare_approval(state)
    append_audit_event(
        state,
        "validation_completed",
        actor="system",
        before_state=ApprovalStatus.NOT_READY.value,
        after_state=approval.status.value,
        changed_fields=[
            "technical_validation",
            "commercial_validation",
            "combined_decision",
        ],
        triggered_rule_ids=decision.triggered_rule_ids,
        details={
            "technical_status": technical.status,
            "commercial_status": commercial.status,
            "decision": decision.status,
        },
    )
    return decision


def analyse_quotation_lines(
    state: QuotationWorkflowState,
    pricing_engine: PricingEngine | None = None,
    *,
    policy: CommercialPolicyVersion | None = None,
    cost_overrides=None,
    pricing_data_version: str = "",
) -> QuotationPricingAnalysis:
    """Run the deterministic multi-line pricing analysis for a quotation."""

    analysis = analyse_quotation_pricing(
        state.draft,
        pricing_engine=pricing_engine,
        policy=policy,
        cost_overrides=cost_overrides,
        pricing_data_version=pricing_data_version,
        quotation_version=state.draft.updated_at.isoformat(),
    )
    state.quotation_pricing = analysis
    state.pricing_explanation = None
    state.combined_decision = None
    state.validation_stale = True
    append_audit_event(
        state,
        "quotation_pricing_completed",
        actor="system",
        before_state=state.approval.status.value,
        after_state=state.approval.status.value,
        changed_fields=["quotation_pricing"],
        details={
            "pricing_run_id": analysis.pricing_run_id,
            "line_count": len(analysis.line_analyses),
            "margin_status": analysis.margin_status,
        },
    )
    return analysis


def judge_quotation(
    state: QuotationWorkflowState,
    *,
    policy: CommercialPolicyVersion | None = None,
    technical_validation_run_id: str = "",
    explanation_provider=None,
) -> CombinedDecision:
    """Apply the deterministic commercial margin gate and record the trace."""

    if state.quotation_pricing is None:
        raise WorkflowOrchestrationError(
            "Run the multi-line pricing analysis before the logical judgement."
        )
    decision = evaluate_commercial_decision(
        state.quotation_pricing,
        state.technical_validation,
        policy=policy or active_commercial_policy(),
        technical_validation_run_id=technical_validation_run_id,
    )
    state.combined_decision = decision
    state.validation_stale = False
    state.pricing_explanation = explain_pricing_decision(
        state.quotation_pricing,
        decision,
        provider=explanation_provider,
    )
    approval = prepare_approval(state)
    append_audit_event(
        state,
        "commercial_decision_completed",
        actor="system",
        before_state=ApprovalStatus.NOT_READY.value,
        after_state=approval.status.value,
        changed_fields=["combined_decision"],
        triggered_rule_ids=decision.triggered_rule_ids,
        details={
            "decision": decision.status,
            "policy_version_id": decision.policy_version_id,
            "evaluated_margin_percent": decision.evaluated_margin_percent,
            "threshold_percent": decision.threshold_percent,
            "pricing_run_id": decision.pricing_run_id,
        },
    )
    return decision


def selected_recommendation_item(
    product_id: str,
    recommendation: QuoteRecommendation | None,
) -> RecommendationItem | None:
    if recommendation is None:
        return None
    return next(
        (
            item
            for item in (
                recommendation.main_model,
                *recommendation.alternatives,
            )
            if item is not None and item.product_id == product_id
        ),
        None,
    )
