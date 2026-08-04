from __future__ import annotations

import logging
from typing import Any

import streamlit as st

from app.approval_workflow import (
    ACTION_APPROVE,
    ACTION_APPROVE_WITH_OVERRIDE,
    ACTION_REJECT,
    ACTION_REQUEST_REVISION,
    APPROVER_ROLES,
    ApprovalWorkflowError,
    approval_reminder_status,
    available_approval_actions,
    prepare_approval,
    submit_approval_action,
)
from app.audit_export import (
    build_customer_quotation_export,
    build_internal_audit_export,
    export_json_bytes,
)
from app.config import DEMO_MODE, PRICING_DATA_MODE, SHOW_INTERNAL_COSTS
from app.conversation_agent import FIELD_QUESTIONS, RequirementConversationAgent
from app.demo_scenarios import (
    DEMO_SCENARIOS,
    SCENARIO_SESSION_KEY,
    apply_demo_price_profile,
    load_demo_scenario,
)
from app.data_loader import load_snapshot, synthetic_snapshot_path
from app.document_generator import generate_quotation_pdf
from app.email_generator import (
    generate_customer_email,
    generate_internal_approval_email,
    generate_reminder_email,
    generate_revision_email,
)
from app.output_context import APPROVED_STATUSES, OutputGenerationError
from app.pricing_data import PricingDataError
from app.pricing_engine import PricingEngine
from app.quotation_models import (
    ApprovalStatus,
    CommercialValidationResult,
    EmailOutput,
    QuotationDraft,
    QuotationWorkflowState,
    TechnicalValidationResult,
    WorkflowStage,
)
from app.recommender import QuoteRecommendation, RecommendationItem
from app.recommender import QuoteRecommender
from app.workflow_orchestrator import (
    WorkflowOrchestrationError,
    analyse_workflow_pricing,
    process_requirement_message,
    select_recommended_product,
    validate_workflow,
)
from app.workflow_state import (
    get_or_initialize_workflow_state,
    reset_workflow_state,
)
from app.workflow_validation import (
    apply_quotation_edits,
)


LOGGER = logging.getLogger(__name__)


st.set_page_config(
    page_title="Quotation Bot",
    page_icon="QB",
    layout="wide",
)


CUSTOM_CSS = """
<style>
:root {
    --quote-ink: #18212f;
    --quote-muted: #5d6878;
    --quote-line: #d8dee8;
    --quote-accent: #f07a22;
    --quote-bg: #f7f8f5;
}
.stApp {
    background:
        radial-gradient(circle at 16% 14%, rgba(240, 122, 34, 0.12), transparent 28%),
        linear-gradient(135deg, #fbfaf5 0%, var(--quote-bg) 46%, #edf3f1 100%);
    color: var(--quote-ink);
}
h1, h2, h3, p, li, div, label, span {
    font-family: "Aptos Display", "Segoe UI Variable Display", "Trebuchet MS", sans-serif;
}
[data-testid="stChatMessage"] {
    border: 1px solid rgba(24, 33, 47, 0.08);
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.78);
    box-shadow: 0 12px 28px rgba(24, 33, 47, 0.07);
}
.status-pill {
    display: inline-flex;
    border: 1px solid var(--quote-line);
    border-radius: 999px;
    padding: 0.18rem 0.65rem;
    margin-right: 0.35rem;
    color: var(--quote-muted);
    background: rgba(255, 255, 255, 0.72);
    font-size: 0.88rem;
}
.status-pill.active {
    border-color: var(--quote-accent);
    color: var(--quote-ink);
    background: rgba(240, 122, 34, 0.12);
}
</style>
"""


@st.cache_resource
def get_conversation_agent() -> RequirementConversationAgent:
    if PRICING_DATA_MODE == "synthetic":
        recommender = QuoteRecommender(
            snapshot=load_snapshot(synthetic_snapshot_path()),
            profile_products={},
        )
        return RequirementConversationAgent(recommender=recommender)
    return RequirementConversationAgent()


@st.cache_resource
def get_pricing_engine() -> PricingEngine:
    return PricingEngine()


def main() -> None:
    state = get_or_initialize_workflow_state(st.session_state)
    _initialize_messages()
    _render_sidebar(state)

    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
    st.title("Quotation Bot")
    st.caption(
        "One deterministic local workflow from requirements through approved "
        "quotation downloads"
    )

    _render_stage_indicator(state)

    st.header("A. Conversation")
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    st.header("B. Quotation Draft")
    _render_main_draft(state.draft)

    recommendation = state.product_recommendation
    if isinstance(recommendation, QuoteRecommendation):
        st.header("C. Product Recommendation")
        _render_product_selection(state, recommendation)
    elif state.draft.product_query:
        st.warning(
            "No product recommendation is available yet. Add product details "
            "in the conversation and try again."
        )
    if state.draft.selected_product_ids:
        _render_quotation_editor(state, recommendation)
        st.header("D. Pricing Analysis")
        _render_pricing_analysis(state, recommendation)
    if state.combined_decision is not None and not state.validation_stale:
        _render_approval_panel(state)
    if state.approval.status != ApprovalStatus.NOT_READY:
        _render_output_stage(state)
    prompt = st.chat_input(
        "Answer the current question or correct a field",
    )
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    agent = get_conversation_agent()
    try:
        result = process_requirement_message(state, prompt, agent)
    except ValueError as error:
        LOGGER.info(
            "Requirement message was rejected (%s).",
            type(error).__name__,
        )
        st.error(f"I could not use that answer: {error}")
        return

    response_parts = list(result.notices)
    if result.next_question:
        response_parts.append(result.next_question)
    elif result.ready_for_analysis:
        response_parts.append(
            "Requirements and product selection are complete. Ready for pricing in the next phase."
        )
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": "\n\n".join(response_parts),
        }
    )
    st.rerun()


def _initialize_messages() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [_welcome_message()]


def _welcome_message() -> dict[str, str]:
    return {
        "role": "assistant",
        "content": FIELD_QUESTIONS["customer_name"],
    }


def _render_sidebar(state: QuotationWorkflowState) -> None:
    with st.sidebar:
        st.title("Quotation workflow")
        if st.button("New quotation", type="primary", use_container_width=True):
            reset_workflow_state(st.session_state)
            st.session_state.pop(SCENARIO_SESSION_KEY, None)
            st.session_state.messages = [_welcome_message()]
            st.rerun()

        st.caption("Current quotation ID")
        st.code(state.draft.quotation_id, language=None)
        st.success(
            "DEMO MODE — synthetic local data"
            if DEMO_MODE
            else "Configured application mode"
        )
        st.caption(f"Pricing data mode: {PRICING_DATA_MODE}")

        st.markdown("#### Workflow progress")
        current_step = _current_step(state)
        for index, label in enumerate(
            (
                "Start quotation",
                "Requirements",
                "Product selection",
                "Pricing",
                "Validation",
                "Human review",
                "Communication",
                "Documents and audit",
            ),
            start=1,
        ):
            marker = "▶" if index == current_step else ("✓" if index < current_step else "○")
            st.write(f"{marker} {index}. {label}")

        if SHOW_INTERNAL_COSTS:
            st.toggle(
                "Show restricted internal pricing",
                key="internal_data_visible",
                help="Available only because SHOW_INTERNAL_COSTS is enabled.",
            )

        st.markdown("#### Demo scenarios")
        scenario_by_name = {
            scenario.name: scenario for scenario in DEMO_SCENARIOS
        }
        selected_name = st.selectbox(
            "Example input",
            options=list(scenario_by_name),
            help="All customer and location values are synthetic.",
        )
        selected_scenario = scenario_by_name[selected_name]
        st.caption(selected_scenario.description)
        if st.button("Load scenario", use_container_width=True):
            load_demo_scenario(
                st.session_state,
                selected_scenario.scenario_id,
            )
            st.session_state.messages = [
                {
                    "role": "user",
                    "content": (
                        f"Load {selected_scenario.name} using synthetic demo values."
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        "The requirements and compatible product configuration "
                        "are ready. Run pricing analysis to continue."
                    ),
                },
            ]
            st.rerun()

        st.divider()
        st.markdown("#### Draft summary")
        _render_sidebar_draft(state.draft)


def _current_step(state: QuotationWorkflowState) -> int:
    if state.approval.status in APPROVED_STATUSES:
        return 8
    if state.approval.status in {
        ApprovalStatus.REJECTED,
        ApprovalStatus.REVISION_REQUESTED,
    }:
        return 7
    if state.approval.status == ApprovalStatus.PENDING_REVIEW:
        return 6
    if state.combined_decision is not None and not state.validation_stale:
        return 6
    if state.pricing_result is not None:
        return 5
    if state.draft.selected_product_ids:
        return 4
    if state.product_recommendation is not None:
        return 3
    if state.draft.customer_name or state.draft.product_query:
        return 2
    return 1


def _render_stage_indicator(state: QuotationWorkflowState) -> None:
    has_product_details = bool(
        state.draft.product_query or state.product_recommendation
    )
    pricing_available = state.pricing_result is not None
    validation_available = state.combined_decision is not None
    approval_available = state.approval.status != ApprovalStatus.NOT_READY
    output_available = state.approval.status in APPROVED_STATUSES
    is_ready = state.current_stage == WorkflowStage.READY_FOR_ANALYSIS
    is_analysed = state.current_stage == WorkflowStage.ANALYSED
    stages = (
        (
            "1. Start",
            not has_product_details and not is_ready and not is_analysed,
        ),
        (
            "2. Requirements",
            has_product_details and not is_ready and not is_analysed,
        ),
        (
            "3. Product",
            (is_ready or is_analysed) and not pricing_available,
        ),
        (
            "4. Pricing",
            (pricing_available or validation_available) and not approval_available,
        ),
        (
            "5. Validation",
            approval_available and not output_available,
        ),
        (
            "6–8. Review and outputs",
            output_available,
        ),
    )
    stage_markup = "".join(
        f'<span class="status-pill{" active" if active else ""}">{label}</span>'
        for label, active in stages
    )
    st.markdown(stage_markup, unsafe_allow_html=True)


def _render_sidebar_draft(draft: QuotationDraft) -> None:
    summary = {
        "Customer": draft.customer_name or "Not provided",
        "Region": draft.region or "Not provided",
        "Product": draft.product_query or "Not provided",
        "Quantity": (
            str(draft.quantity)
            if "quantity" not in draft.missing_fields
            else "Not confirmed"
        ),
        "Currency": (
            draft.currency
            if "currency" not in draft.missing_fields
            else "Not confirmed"
        ),
    }
    for label, value in summary.items():
        st.markdown(f"**{label}:** {value}")


def _render_main_draft(draft: QuotationDraft) -> None:
    st.dataframe(
        [
            {
                "Customer": draft.customer_name or "Not provided",
                "Region": draft.region or "Not provided",
                "Product request": draft.product_query or "Not provided",
                "Selected product": ", ".join(draft.selected_product_ids)
                or "Not selected",
                "Quantity": draft.quantity,
                "Currency": draft.currency,
                "Incoterm": draft.incoterm or "Not provided",
                "Delivery": draft.delivery_location or "Not provided",
            }
        ],
        use_container_width=True,
        hide_index=True,
    )
    if draft.missing_fields:
        st.warning(
            "Still required: "
            + ", ".join(
                field_name.replace("_", " ")
                for field_name in draft.missing_fields
            )
        )
    elif not draft.selected_product_ids:
        st.info("Requirements are complete. Select a recommended product next.")
    else:
        st.success("Requirements are complete and a product is selected.")


def _render_product_selection(
    state: QuotationWorkflowState,
    recommendation: QuoteRecommendation,
) -> None:
    items = _selectable_items(recommendation)
    if not items:
        st.info("Add more product detail to obtain a catalog recommendation.")
        return

    main_model = recommendation.main_model
    if main_model:
        st.markdown(
            f"**Recommended:** {main_model.short_description}  \n"
            f"Product ID: `{main_model.product_id}`"
        )
        st.info(f"Why this product: {main_model.reason}")
    if recommendation.accessories:
        with st.expander("Configured supporting components", expanded=False):
            for item in recommendation.accessories:
                st.write(
                    f"- {item.short_description} ({item.product_id}): {item.reason}"
                )
    if recommendation.alternatives:
        st.caption("Alternatives are available in the selector below.")

    item_by_id = {item.product_id: item for item in items}
    selected_id = st.selectbox(
        "Choose a product",
        options=list(item_by_id),
        format_func=lambda product_id: (
            f"{item_by_id[product_id].short_description} ({product_id})"
        ),
        key=f"product_choice_{state.draft.quotation_id}",
    )
    if st.button("Use selected product", type="primary"):
        agent = get_conversation_agent()
        try:
            select_recommended_product(
                state,
                selected_id,
                recommendation,
                agent,
            )
        except ValueError as error:
            LOGGER.info(
                "Product selection was rejected (%s).",
                type(error).__name__,
            )
            st.error(f"I could not select that product: {error}")
            return

        message = (
            "Product selected. Requirements are complete and ready for pricing "
            "analysis."
            if state.current_stage == WorkflowStage.READY_FOR_ANALYSIS
            else "Product selected. I will continue collecting the remaining requirements."
        )
        st.session_state.messages.append(
            {"role": "assistant", "content": message}
        )
        st.rerun()


def _render_quotation_editor(
    state: QuotationWorkflowState,
    recommendation: QuoteRecommendation | None,
) -> None:
    with st.expander("Edit quotation and revalidate", expanded=False):
        items = _selectable_items(recommendation) if recommendation else ()
        item_by_id = {item.product_id: item for item in items}
        current_product = state.draft.selected_product_ids[0]
        if current_product not in item_by_id:
            item_by_id[current_product] = RecommendationItem(
                product_id=current_product,
                short_description=current_product,
                quantity=state.draft.quantity,
                step_id=None,
                option_group=None,
                reason="Current selection",
                source={},
            )

        with st.form(f"quotation_editor_{state.draft.quotation_id}"):
            selected_product = st.selectbox(
                "Selected product",
                options=list(item_by_id),
                index=list(item_by_id).index(current_product),
                format_func=lambda product_id: (
                    f"{item_by_id[product_id].short_description} ({product_id})"
                ),
            )
            quantity = st.number_input(
                "Quantity",
                min_value=1,
                step=1,
                value=state.draft.quantity,
            )
            proposed_default = (
                state.draft.proposed_unit_price
                if state.draft.proposed_unit_price is not None
                else (
                    state.pricing_result.recommended_unit_price
                    if state.pricing_result
                    and state.pricing_result.recommended_unit_price is not None
                    else 0.0
                )
            )
            use_price_override = st.checkbox(
                "Use proposed unit-price override",
                value=state.draft.proposed_unit_price is not None,
            )
            proposed_price = st.number_input(
                "Proposed unit price",
                min_value=0.0,
                step=100.0,
                value=float(proposed_default),
                disabled=not use_price_override,
                help="Demo proposal only; saving requires pricing and validation to run again.",
            )
            currency_options = list(
                dict.fromkeys(
                    [state.draft.currency, "USD", "SGD", "RMB", "CNY", "EUR"]
                )
            )
            currency = st.selectbox(
                "Currency",
                options=currency_options,
                index=currency_options.index(state.draft.currency),
            )
            incoterms = list(
                dict.fromkeys(
                    [state.draft.incoterm, "EXW", "FCA", "FOB", "CIF", "DAP", "DDP"]
                )
            )
            incoterm = st.selectbox(
                "Incoterm",
                options=incoterms,
                index=incoterms.index(state.draft.incoterm),
            )
            delivery_location = st.text_input(
                "Delivery location",
                value=state.draft.delivery_location,
            )
            submitted = st.form_submit_button("Save edits and require re-analysis")

        if submitted:
            try:
                changed_fields = apply_quotation_edits(
                    state,
                    selected_product_ids=[selected_product],
                    quantity=int(quantity),
                    proposed_unit_price=(
                        float(proposed_price) if use_price_override else None
                    ),
                    currency=currency,
                    incoterm=incoterm,
                    delivery_location=delivery_location,
                )
            except ValueError as error:
                st.error(f"The quotation could not be updated: {error}")
                return
            if changed_fields:
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            "Quotation edits were saved. Previous pricing and validation "
                            "were cleared; run analysis again."
                        ),
                    }
                )
                st.rerun()
            st.info("No quotation fields changed.")


def _render_pricing_analysis(
    state: QuotationWorkflowState,
    recommendation: QuoteRecommendation | None,
) -> None:
    st.caption(
        "Deterministic demo analysis using archived local data and configurable demo policies."
    )

    approval_complete = state.approval.status in {
        ApprovalStatus.APPROVED,
        ApprovalStatus.APPROVED_WITH_OVERRIDE,
        ApprovalStatus.REJECTED,
        ApprovalStatus.REVISION_REQUESTED,
    }
    if st.button(
        "Analyse quotation",
        type="primary",
        disabled=approval_complete,
    ):
        try:
            result = analyse_workflow_pricing(
                state,
                get_pricing_engine(),
                recommendation,
            )
            scenario_id = st.session_state.get(SCENARIO_SESSION_KEY)
            apply_demo_price_profile(
                state,
                scenario_id,
            )
            if scenario_id:
                st.session_state.pop(SCENARIO_SESSION_KEY, None)
        except (PricingDataError, WorkflowOrchestrationError) as error:
            LOGGER.warning(
                "Pricing analysis failed (%s).",
                type(error).__name__,
            )
            st.error(f"Pricing analysis is unavailable: {error}")
            return
        st.rerun()

    result = state.pricing_result
    if result is None:
        return

    currency = result.currency or "USD"
    metrics = st.columns(4)
    metrics[0].metric("Selected product", state.draft.selected_product_ids[0])
    metrics[1].metric("Quantity", state.draft.quantity)
    metrics[2].metric(
        "Reference list price",
        _format_money(result.reference_list_price, currency),
    )
    metrics[3].metric("Confidence", result.confidence_label or "Low")

    price_metrics = st.columns(4)
    price_metrics[0].metric(
        "Recommended unit price",
        _format_money(result.recommended_unit_price, currency),
    )
    price_metrics[1].metric(
        "Total",
        _format_money(result.total_price, currency),
    )
    price_metrics[2].metric(
        "Gross margin",
        _format_percent(result.gross_margin_percent),
    )
    price_metrics[3].metric(
        "Discount vs list",
        _format_percent(result.discount_percent),
    )

    if result.recommended_unit_price is None:
        st.error("Pricing is unavailable for the selected product.")

    if result.internal_evidence:
        st.markdown("#### Internal comparable evidence")
        st.caption(
            f"{result.comparable_count} normalized comparable records supported "
            "the deterministic price. Source workbook metadata is intentionally hidden."
        )
        st.dataframe(
            [
                {
                    "Product ID": item.product_id,
                    "Description": item.description,
                    "List price": item.list_price,
                    "Currency": item.currency,
                }
                for item in result.internal_evidence
            ],
            use_container_width=True,
            hide_index=True,
        )

    if result.assumptions:
        with st.expander("How the price was calculated", expanded=True):
            st.write(
                "The engine selects strong exact/description comparables, uses "
                "the configured median price hierarchy, applies the quantity "
                "adjustment, and then enforces available demo floors."
            )
            for assumption in result.assumptions:
                st.write(f"- {assumption}")
    for warning in result.warnings:
        st.warning(warning)

    if _internal_data_visible():
        with st.expander("Internal analysis — restricted", expanded=False):
            st.metric(
                "Estimated unit cost",
                _format_money(result.estimated_cost, currency),
            )
            st.metric(
                "Gross margin amount",
                _format_money(result.gross_margin_amount, currency),
            )

    st.divider()
    st.header("E. Validation")
    if st.button(
        "Run technical and commercial validation",
        type="primary",
        disabled=result.recommended_unit_price is None,
    ):
        conversation_agent = get_conversation_agent()
        try:
            validate_workflow(
                state,
                recommendation,
                conversation_agent.recommender.engine,
            )
        except WorkflowOrchestrationError as error:
            LOGGER.info(
                "Validation could not start (%s).",
                type(error).__name__,
            )
            st.error(f"Validation could not be completed: {error}")
            return
        st.rerun()

    if state.validation_stale:
        st.info("Validation is pending or stale. Run validation before proceeding.")
    elif state.technical_validation and state.commercial_validation:
        _render_validation_results(
            state.technical_validation,
            state.commercial_validation,
            state,
        )


def _selectable_items(
    recommendation: QuoteRecommendation,
) -> tuple[RecommendationItem, ...]:
    unique: dict[str, RecommendationItem] = {}
    for item in (recommendation.main_model, *recommendation.alternatives):
        if item is not None:
            unique.setdefault(item.product_id, item)
    return tuple(unique.values())


def _render_validation_results(
    technical: TechnicalValidationResult,
    commercial: CommercialValidationResult,
    state: QuotationWorkflowState,
) -> None:
    st.subheader("Validation")
    technical_icon = _status_icon(technical.status)
    st.markdown(
        f"### {technical_icon} Technical validation: "
        f"{technical.status.replace('_', ' ').upper()}"
    )
    if technical.passed_checks:
        st.markdown("**Passed checks**")
        for check in technical.passed_checks:
            st.write(f"- PASS: {check}")
    if technical.warnings:
        st.markdown("**Warnings**")
        for warning in technical.warnings:
            st.write(f"- WARNING: {warning}")
    if technical.errors:
        st.markdown("**Errors**")
        for error in technical.errors:
            st.write(f"- ERROR: {error}")
    if technical.not_evaluated_checks:
        st.markdown("**Not evaluated**")
        for check in technical.not_evaluated_checks:
            st.write(f"- NOT EVALUATED: {check}")

    st.markdown(
        f"### {_status_icon(commercial.status)} Commercial validation: "
        f"{commercial.status.replace('_', ' ').upper()}"
    )
    st.dataframe(
        [
            {
                "Rule": rule.rule_id,
                "Check": rule.name,
                "Result": rule.status.replace("_", " ").upper(),
                "Explanation": rule.message,
            }
            for rule in commercial.rule_results
        ],
        use_container_width=True,
        hide_index=True,
    )

    decision = state.combined_decision
    if decision:
        st.markdown("### Logical judgement")
        st.markdown(
            f"## {_status_icon(decision.status)} "
            f"{decision.status.replace('_', ' ').upper()}"
        )
        st.write(decision.summary)
        if decision.triggered_rule_ids:
            st.write(
                "Triggered rules: " + ", ".join(decision.triggered_rule_ids)
            )
        st.info(f"Next action: {decision.recommended_next_action}")
        if decision.approval_required:
            st.warning(
                "Review is required before approval can be completed."
            )
        elif decision.status == "blocked":
            st.error(
                "This quotation is blocked. Correct the stated issues and "
                "revalidate, or request revision."
            )
        else:
            st.success("The quotation can proceed to human review.")


def _render_approval_panel(state: QuotationWorkflowState) -> None:
    approval = prepare_approval(state)
    st.divider()
    st.header("F. Human Review")
    st.warning(
        "Demo-only simulated approval. The selected role is not authenticated "
        "and this action is not a company authorization."
    )
    recommended_price = state.pricing_result.recommended_unit_price
    proposed_price = (
        state.draft.proposed_unit_price
        if state.draft.proposed_unit_price is not None
        else recommended_price
    )
    approval_metrics = st.columns(3)
    approval_metrics[0].metric(
        "Recommended unit price",
        _format_money(recommended_price, state.pricing_result.currency),
    )
    approval_metrics[1].metric(
        "Proposed/final unit price",
        _format_money(proposed_price, state.pricing_result.currency),
    )
    approval_metrics[2].metric(
        "Validation decision",
        state.combined_decision.status.replace("_", " ").upper(),
    )

    if state.combined_decision.triggered_rule_ids:
        st.write(
            "Review reasons: "
            + ", ".join(state.combined_decision.triggered_rule_ids)
        )

    if approval.status == ApprovalStatus.PENDING_REVIEW:
        st.info(approval_reminder_status(state))
        actions = available_approval_actions(state)
        with st.form(f"approval_{state.draft.quotation_id}"):
            actor_role = st.selectbox("Approver role", APPROVER_ROLES)
            actor_name = st.text_input("Approver name (optional)")
            final_price = st.number_input(
                "Final unit price",
                min_value=0.0,
                value=float(proposed_price or 0.0),
                step=100.0,
            )
            reason = st.text_area(
                "Reason / override justification",
                help=(
                    "Mandatory for override, revision request, and rejection."
                ),
            )
            submitted_action = None
            action_labels = {
                ACTION_APPROVE: "Approve",
                ACTION_APPROVE_WITH_OVERRIDE: "Approve with override",
                ACTION_REQUEST_REVISION: "Request revision",
                ACTION_REJECT: "Reject",
            }
            for action in actions:
                if st.form_submit_button(action_labels[action]):
                    submitted_action = action

        if submitted_action:
            try:
                submit_approval_action(
                    state,
                    action=submitted_action,
                    actor_role=actor_role,
                    actor_name=actor_name,
                    reason=reason,
                    final_unit_price=float(final_price),
                )
            except ApprovalWorkflowError as error:
                st.error(f"Approval action was not accepted: {error}")
                return
            st.rerun()
        return

    status_label = approval.status.value.replace("_", " ").upper()
    st.markdown(f"### {_status_icon(approval.status.value)} {status_label}")
    if approval.actor:
        st.write(f"Actor: {approval.actor} ({approval.actor_role})")
    if approval.final_price is not None:
        st.write(
            "Final approved unit price: "
            + _format_money(
                approval.final_price,
                state.pricing_result.currency,
            )
        )
    if approval.reason:
        st.write(f"Reason: {approval.reason}")
    if approval.timestamp:
        st.caption(f"Recorded at {approval.timestamp.isoformat()}")


def _render_output_stage(state: QuotationWorkflowState) -> None:
    st.divider()
    st.header("G. Communication")
    status = state.approval.status
    try:
        if status in APPROVED_STATUSES or status == ApprovalStatus.PENDING_REVIEW:
            internal_email = generate_internal_approval_email(state)
            state.internal_email = internal_email
            st.markdown("### Internal approval email preview")
            _render_email_preview(internal_email, state, "internal")

        if status == ApprovalStatus.PENDING_REVIEW:
            reminder_email = generate_reminder_email(state)
            st.markdown("### Internal reminder email preview")
            _render_email_preview(reminder_email, state, "reminder")

        if status in {
            ApprovalStatus.REJECTED,
            ApprovalStatus.REVISION_REQUESTED,
        }:
            revision_email = generate_revision_email(state)
            st.markdown("### Rejection / revision notification preview")
            _render_email_preview(revision_email, state, "revision")

        if status in APPROVED_STATUSES:
            customer_email = generate_customer_email(state)
            quotation_pdf = generate_quotation_pdf(state)
            state.customer_email = customer_email
            st.markdown("### Customer quotation email preview")
            _render_email_preview(customer_email, state, "customer")
            st.header("H. Documents and Audit")
            _render_customer_quotation_preview(state)
            st.download_button(
                "Download quotation PDF",
                data=quotation_pdf.bytes_data,
                file_name=quotation_pdf.filename,
                mime=quotation_pdf.mime_type,
            )
            _render_audit_exports(state, include_customer=True)
        elif status in {
            ApprovalStatus.REJECTED,
            ApprovalStatus.REVISION_REQUESTED,
        }:
            st.header("H. Documents and Audit")
            st.info(
                "A customer PDF is unavailable because this quotation was not approved."
            )
            _render_audit_exports(state, include_customer=False)
    except OutputGenerationError as error:
        LOGGER.warning(
            "Output generation failed (%s).",
            type(error).__name__,
        )
        st.error(f"Outputs could not be generated: {error}")


def _render_email_preview(
    email: EmailOutput,
    state: QuotationWorkflowState,
    preview_key: str,
) -> None:
    st.text_area(
        "Preview",
        value=f"Subject: {email.subject}\n\n{email.body}",
        height=260,
        disabled=True,
        key=(
            f"{preview_key}_email_{state.draft.quotation_id}_"
            f"{state.approval.status.value}"
        ),
    )


def _render_customer_quotation_preview(state: QuotationWorkflowState) -> None:
    pricing = state.pricing_result
    if pricing is None:
        st.error("Current pricing is required for the quotation preview.")
        return
    final_price = state.approval.final_price
    total = (
        final_price * state.draft.quantity
        if final_price is not None
        else None
    )
    st.markdown("### Final quotation preview")
    st.dataframe(
        [
            {
                "Product ID": ", ".join(state.draft.selected_product_ids),
                "Description": state.draft.product_query,
                "Quantity": state.draft.quantity,
                "Unit price": _format_money(final_price, pricing.currency),
                "Total": _format_money(total, pricing.currency),
                "Incoterm": state.draft.incoterm,
            }
        ],
        use_container_width=True,
        hide_index=True,
    )


def _render_audit_exports(
    state: QuotationWorkflowState,
    *,
    include_customer: bool,
) -> None:
    st.subheader("Structured audit exports")
    columns = st.columns(2 if include_customer else 1)
    columns[0].download_button(
        "Download internal audit JSON",
        data=export_json_bytes(build_internal_audit_export(state)),
        file_name=f"{state.draft.quotation_id}-internal-audit.json",
        mime="application/json",
    )
    if include_customer:
        columns[1].download_button(
            "Download customer quotation data JSON",
            data=export_json_bytes(build_customer_quotation_export(state)),
            file_name=f"{state.draft.quotation_id}-customer-data.json",
            mime="application/json",
        )


def _internal_data_visible() -> bool:
    return SHOW_INTERNAL_COSTS and bool(
        st.session_state.get("internal_data_visible", False)
    )


def _status_icon(status: str) -> str:
    if status in {"pass", "valid", "approved"}:
        return "✅"
    if status in {
        "pass_with_warnings",
        "valid_with_warnings",
        "approved_with_override",
    }:
        return "⚠️"
    if status in {
        "review_required",
        "not_fully_evaluated",
        "revision_requested",
        "pending_review",
    }:
        return "🔎"
    if status in {"blocked", "invalid", "rejected"}:
        return "⛔"
    return "ℹ️"


def _format_money(value: float | None, currency: str) -> str:
    return "Unavailable" if value is None else f"{currency} {value:,.2f}"


def _format_percent(value: float | None) -> str:
    return "Unavailable" if value is None else f"{value:.2f}%"


if __name__ == "__main__":
    main()
