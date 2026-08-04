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
from app.ingestion.pricing_source import resolve_pricing_source
from app.pricing_data import PricingDataError
from app.pricing_engine import PricingEngine
from app.line_items import (
    LineItemError,
    add_line_item,
    build_recommendations,
    quotation_total,
    remove_line_item,
    update_line_item,
)
from app.quotation_models import (
    ApprovalStatus,
    CommercialValidationResult,
    EmailOutput,
    LineItemCategory,
    QuotationDraft,
    QuotationWorkflowState,
    RecommendationStatus,
    TechnicalValidationResult,
    WorkflowStage,
)
from app.requirement_fields import (
    ALLOWED_CURRENCIES,
    ALLOWED_INCOTERMS,
)
from app.requirement_intake import pending_confirmations
from app.recommender import QuoteRecommendation, RecommendationItem
from app.recommender import QuoteRecommender
from app.workflow_orchestrator import (
    WorkflowOrchestrationError,
    analyse_workflow_pricing,
    apply_structured_requirements,
    confirm_requirement_candidate,
    process_requirement_message,
    select_recommended_product,
    validate_workflow,
)
from app.repositories.interfaces import QuotationVersionConflictError
from app.services.workflow_session import (
    duplicate_active_quotation,
    ensure_schema,
    get_active_quotation,
    persist_workflow_state,
    start_new_quotation,
)
from app.workflow_validation import (
    apply_quotation_edits,
)


LOGGER = logging.getLogger(__name__)


st.set_page_config(
    page_title="Quotation Bot",
    page_icon=":material/request_quote:",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": (
            "Quotation Bot — a deterministic, rule-backed quotation assistant "
            "running on synthetic demo data."
        )
    },
)


WORKFLOW_STEPS = (
    "Start quotation",
    "Requirements",
    "Product selection",
    "Pricing",
    "Validation",
    "Human review",
    "Communication",
    "Documents and audit",
)


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
    # The engine prices against the explicitly activated published dataset.
    # When no version is active the synthetic development dataset is used.
    source = resolve_pricing_source()
    return PricingEngine(records=source.records)



def _persist_and_rerun(
    state: QuotationWorkflowState,
    *,
    event_type: str = "quotation_updated",
    changed_fields: tuple[str, ...] = (),
) -> None:
    """Write the mutated state to the database, then re-render.

    Every rerun boundary goes through here so no workflow change exists only
    in the browser session.
    """

    try:
        persist_workflow_state(
            st.session_state,
            state,
            event_type=event_type,
            changed_fields=changed_fields,
        )
    except QuotationVersionConflictError as error:
        st.error(str(error), icon=":material/sync_problem:")
        return
    st.rerun()


def main() -> None:
    # Trusted quotation state lives in the database. Session state holds only
    # the active quotation reference; it is reloaded on every interaction.
    ensure_schema()
    state = get_active_quotation(st.session_state).state
    _initialize_messages()
    _render_sidebar(state)

    st.title(":material/request_quote: Quotation Bot")
    st.caption(
        "One deterministic local workflow from requirements through approved "
        "quotation downloads"
    )

    _render_stage_indicator(state)
    st.divider()

    conversation_column, workspace_column = st.columns([2, 3], gap="large")

    with conversation_column:
        st.subheader("A. Conversation")
        with st.container(height=520, border=True):
            for message in st.session_state.messages:
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

    with workspace_column:
        st.subheader("B. Quotation draft")
        with st.container(border=True):
            _render_main_draft(state.draft)

        _render_pending_confirmations(state)
        _render_structured_requirement_form(state)

        recommendation = state.product_recommendation
        _render_line_item_workspace(state, recommendation)
        if isinstance(recommendation, QuoteRecommendation):
            st.subheader("C. Product recommendation")
            with st.container(border=True):
                _render_product_selection(state, recommendation)
        elif state.draft.product_query:
            st.warning(
                "No product recommendation is available yet. Add product details "
                "in the conversation and try again.",
                icon=":material/search_off:",
            )
        if state.draft.selected_product_ids:
            _render_quotation_editor(state, recommendation)
            st.subheader("D. Pricing analysis")
            with st.container(border=True):
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
        st.error(
            f"I could not use that answer: {error}",
            icon=":material/error:",
        )
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
    _persist_and_rerun(state, event_type="requirements_updated")


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
        st.header("Quotation workflow")
        if st.button(
            "New quotation",
            type="primary",
            icon=":material/add:",
            use_container_width=True,
        ):
            start_new_quotation(st.session_state)
            st.session_state.pop(SCENARIO_SESSION_KEY, None)
            st.session_state.messages = [_welcome_message()]
            st.rerun()

        duplicate_column, version_column = st.columns(2)
        with duplicate_column:
            if st.button(
                "Duplicate",
                icon=":material/content_copy:",
                use_container_width=True,
                help=(
                    "Copy requirements and line items into a new draft. "
                    "Pricing and approval are not copied."
                ),
            ):
                duplicate_active_quotation(
                    st.session_state, state.draft.quotation_id
                )
                st.session_state.messages = [_welcome_message()]
                st.rerun()
        with version_column:
            if st.button(
                "New version",
                icon=":material/difference:",
                use_container_width=True,
                help="Clone as a new version, keeping the audit lineage.",
            ):
                duplicate_active_quotation(
                    st.session_state,
                    state.draft.quotation_id,
                    as_new_version=True,
                )
                st.session_state.messages = [_welcome_message()]
                st.rerun()

        st.caption("Current quotation ID")
        st.code(state.draft.quotation_id, language=None)
        st.success(
            "DEMO MODE — synthetic local data"
            if DEMO_MODE
            else "Configured application mode",
            icon=":material/verified_user:",
        )
        st.caption(f"Pricing data mode: {PRICING_DATA_MODE}")

        st.divider()
        current_step = _current_step(state)
        total_steps = len(WORKFLOW_STEPS)
        st.markdown("#### Workflow progress")
        st.progress(
            current_step / total_steps,
            text=f"Step {current_step} of {total_steps}: "
            f"{WORKFLOW_STEPS[current_step - 1]}",
        )
        with st.expander("All workflow steps", expanded=False):
            for index, label in enumerate(WORKFLOW_STEPS, start=1):
                if index < current_step:
                    marker = ":material/check_circle:"
                elif index == current_step:
                    marker = ":material/play_circle:"
                else:
                    marker = ":material/radio_button_unchecked:"
                st.markdown(f"{marker} {index}. {label}")

        if SHOW_INTERNAL_COSTS:
            st.toggle(
                "Show restricted internal pricing",
                key="internal_data_visible",
                help="Available only because SHOW_INTERNAL_COSTS is enabled.",
            )

        st.divider()
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
        if st.button(
            "Load scenario",
            icon=":material/download:",
            use_container_width=True,
        ):
            # The demo helper builds its state in a throwaway mapping; the
            # result is then persisted as a new quotation rather than being
            # parked in Streamlit session state.
            demo_state = load_demo_scenario(
                {},
                selected_scenario.scenario_id,
            )
            start_new_quotation(st.session_state, state=demo_state)
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
        with st.container(border=True):
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
    columns = st.columns(len(stages), gap="small")
    for column, (label, active) in zip(columns, stages):
        with column:
            if active:
                st.markdown(f"**:blue[:material/adjust: {label}]**")
            else:
                st.markdown(f":gray[:material/circle: {label}]")


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
        st.markdown(f"**{label}**  \n:gray[{value}]")


def _render_pending_confirmations(state: QuotationWorkflowState) -> None:
    """Low-confidence Agent 1 candidates must be confirmed before use."""

    pending = pending_confirmations(state.draft)
    if not pending:
        return
    st.subheader("B1. Suggestions awaiting confirmation")
    with st.container(border=True):
        for item in pending:
            st.markdown(f"**{item.question}**")
            st.caption(
                f"Source: {item.source} · confidence {item.confidence:.0%}"
            )
            accept_column, discard_column = st.columns(2)
            with accept_column:
                if st.button(
                    "Confirm",
                    key=f"confirm_{item.field_name}",
                    icon=":material/check:",
                ):
                    confirm_requirement_candidate(
                        state, item.field_name, accept=True
                    )
                    _persist_and_rerun(
                        state,
                        event_type="requirement_confirmed",
                        changed_fields=(item.field_name,),
                    )
            with discard_column:
                if st.button(
                    "Discard",
                    key=f"discard_{item.field_name}",
                    icon=":material/close:",
                ):
                    confirm_requirement_candidate(
                        state, item.field_name, accept=False
                    )
                    _persist_and_rerun(
                        state, event_type="requirement_discarded"
                    )


def _render_structured_requirement_form(state: QuotationWorkflowState) -> None:
    """Structured alternative to conversational entry.

    Both entry modes call the same merge logic, so they update the same
    quotation domain model.
    """

    draft = state.draft
    with st.expander(
        "Structured requirement form",
        expanded=False,
        icon=":material/list_alt:",
    ):
        with st.form(f"requirement_form_{draft.quotation_id}"):
            left, right = st.columns(2, gap="medium")
            with left:
                customer_name = st.text_input(
                    "Customer name", value=draft.customer_name
                )
                region = st.text_input("Region", value=draft.region)
                product_query = st.text_area(
                    "Product request", value=draft.product_query, height=80
                )
                quantity = st.number_input(
                    "Quantity", min_value=1, step=1, value=max(draft.quantity, 1)
                )
                intended_use = st.text_input(
                    "Intended use", value=draft.intended_use
                )
                budget_notes = st.text_input(
                    "Budget notes", value=draft.budget_notes
                )
            with right:
                currency_options = list(ALLOWED_CURRENCIES)
                currency = st.selectbox(
                    "Currency",
                    options=currency_options,
                    index=(
                        currency_options.index(draft.currency)
                        if draft.currency in currency_options
                        else 0
                    ),
                )
                incoterm_options = ["", *ALLOWED_INCOTERMS]
                incoterm = st.selectbox(
                    "Incoterm",
                    options=incoterm_options,
                    index=(
                        incoterm_options.index(draft.incoterm)
                        if draft.incoterm in incoterm_options
                        else 0
                    ),
                )
                delivery_location = st.text_input(
                    "Delivery location", value=draft.delivery_location
                )
                requested_accessories = st.text_input(
                    "Requested accessories (comma separated)",
                    value=", ".join(draft.requested_accessories),
                )
                requested_services = st.text_input(
                    "Requested services (comma separated)",
                    value=", ".join(draft.requested_services),
                )
                constraints = st.text_input(
                    "Other constraints (comma separated)",
                    value=", ".join(draft.constraints),
                )
            submitted = st.form_submit_button(
                "Apply requirement form",
                type="primary",
                icon=":material/save:",
            )

        if not submitted:
            return

        outcome = apply_structured_requirements(
            state,
            {
                "customer_name": customer_name,
                "region": region,
                "product_query": product_query,
                "quantity": quantity,
                "currency": currency,
                "incoterm": incoterm,
                "delivery_location": delivery_location,
                "intended_use": intended_use,
                "budget_notes": budget_notes,
                "requested_accessories": requested_accessories,
                "requested_services": requested_services,
                "constraints": constraints,
            },
            get_conversation_agent(),
        )
        for rejected in outcome.rejected:
            st.error(
                f"{rejected.field_name}: {rejected.reason}",
                icon=":material/error:",
            )
        if not outcome.changed_fields:
            return
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": (
                    "I updated these requirements from the form: "
                    + ", ".join(outcome.changed_fields)
                ),
            }
        )
        _persist_and_rerun(
            state,
            event_type="requirements_form_submitted",
            changed_fields=tuple(outcome.changed_fields),
        )


RECOMMENDATION_STATUS_ICONS = {
    RecommendationStatus.REQUIRED: ":material/priority_high:",
    RecommendationStatus.RECOMMENDED: ":material/thumb_up:",
    RecommendationStatus.OPTIONAL: ":material/add_circle:",
    RecommendationStatus.INCOMPATIBLE: ":material/block:",
    RecommendationStatus.NOT_EVALUATED: ":material/help:",
}


def _render_line_item_workspace(
    state: QuotationWorkflowState,
    recommendation: QuoteRecommendation | None,
) -> None:
    engine = get_conversation_agent().recommender.engine
    st.subheader("B2. Quotation line items")
    with st.container(border=True):
        if not state.draft.line_items:
            st.caption("No line items yet.")
        for item in state.draft.line_items:
            with st.container(border=True):
                st.markdown(
                    f"**{item.description or item.product_id}** "
                    f"(`{item.category.value.replace('_', ' ')}`)"
                )
                quantity_column, price_column, remove_column = st.columns(
                    [2, 2, 1], gap="small"
                )
                with quantity_column:
                    quantity = st.number_input(
                        "Quantity",
                        min_value=1,
                        step=1,
                        value=item.quantity,
                        key=f"qty_{item.line_id}",
                    )
                with price_column:
                    unit_price = st.number_input(
                        "Unit price",
                        min_value=0.0,
                        step=100.0,
                        value=float(item.unit_price or 0.0),
                        key=f"price_{item.line_id}",
                    )
                with remove_column:
                    if st.button(
                        "Remove",
                        key=f"remove_{item.line_id}",
                        icon=":material/delete:",
                    ):
                        remove_line_item(state, item.line_id)
                        _persist_and_rerun(
                            state,
                            event_type="line_item_removed",
                            changed_fields=("line_items",),
                        )
                if st.button(
                    "Apply line changes",
                    key=f"apply_{item.line_id}",
                    icon=":material/check:",
                ):
                    try:
                        update_line_item(
                            state,
                            item.line_id,
                            quantity=int(quantity),
                            unit_price=float(unit_price),
                        )
                    except LineItemError as error:
                        st.error(str(error), icon=":material/error:")
                    else:
                        _persist_and_rerun(
                            state,
                            event_type="line_item_updated",
                            changed_fields=("line_items",),
                        )

        if state.draft.line_items:
            st.markdown(
                f"**Committed total**: {quotation_total(state.draft):,.2f} "
                f"{state.draft.currency}"
            )

        _render_line_item_recommendations(state, recommendation, engine)
        _render_manual_line_item_form(state, engine)


def _render_line_item_recommendations(
    state: QuotationWorkflowState,
    recommendation: QuoteRecommendation | None,
    engine,
) -> None:
    lines = build_recommendations(state.draft, recommendation, engine)
    if not lines:
        return
    with st.expander(
        "Recommended additions",
        expanded=False,
        icon=":material/recommend:",
    ):
        for line in lines:
            icon = RECOMMENDATION_STATUS_ICONS[line.status]
            st.markdown(
                f"{icon} **{line.description}** (`{line.product_id}`) — "
                f":gray[{line.status.value.replace('_', ' ')}]"
            )
            if line.status is RecommendationStatus.INCOMPATIBLE:
                st.caption("This item cannot be added to the configuration.")
                continue
            if st.button(
                "Add to quotation",
                key=f"add_rec_{line.product_id}",
                icon=":material/add:",
            ):
                try:
                    add_line_item(
                        state,
                        product_id=line.product_id,
                        description=line.description,
                        category=line.category,
                        quantity=line.quantity,
                        source="recommendation",
                        engine=engine,
                    )
                except LineItemError as error:
                    st.error(str(error), icon=":material/error:")
                else:
                    _persist_and_rerun(
                        state,
                        event_type="line_item_added",
                        changed_fields=("line_items",),
                    )


def _render_manual_line_item_form(
    state: QuotationWorkflowState,
    engine,
) -> None:
    with st.expander(
        "Add a service or commercial line",
        expanded=False,
        icon=":material/add_circle:",
    ):
        with st.form(f"line_item_form_{state.draft.quotation_id}"):
            category = st.selectbox(
                "Line type",
                options=[item.value for item in LineItemCategory],
                index=[item.value for item in LineItemCategory].index(
                    LineItemCategory.SERVICE.value
                ),
            )
            product_id = st.text_input("Product id (optional for services)")
            description = st.text_input("Description")
            quantity = st.number_input("Quantity", min_value=1, step=1, value=1)
            unit_price = st.number_input(
                "Unit price", min_value=0.0, step=100.0, value=0.0
            )
            is_optional = st.checkbox("Optional line", value=False)
            submitted = st.form_submit_button(
                "Add line item", icon=":material/add:"
            )
        if not submitted:
            return
        try:
            add_line_item(
                state,
                product_id=product_id,
                description=description,
                category=LineItemCategory(category),
                quantity=int(quantity),
                unit_price=float(unit_price) or None,
                is_optional=is_optional,
                engine=engine,
            )
        except LineItemError as error:
            st.error(str(error), icon=":material/error:")
            return
        _persist_and_rerun(
            state,
            event_type="line_item_added",
            changed_fields=("line_items",),
        )


def _render_main_draft(draft: QuotationDraft) -> None:
    fields = (
        ("Customer", draft.customer_name or "Not provided"),
        ("Region", draft.region or "Not provided"),
        ("Product request", draft.product_query or "Not provided"),
        (
            "Selected product",
            ", ".join(draft.selected_product_ids) or "Not selected",
        ),
        ("Quantity", str(draft.quantity)),
        ("Currency", draft.currency),
        ("Incoterm", draft.incoterm or "Not provided"),
        ("Delivery", draft.delivery_location or "Not provided"),
    )
    columns = st.columns(4, gap="medium")
    for index, (label, value) in enumerate(fields):
        with columns[index % 4]:
            st.markdown(f"**{label}**  \n:gray[{value}]")

    if draft.missing_fields:
        st.warning(
            "Still required: "
            + ", ".join(
                field_name.replace("_", " ")
                for field_name in draft.missing_fields
            ),
            icon=":material/pending_actions:",
        )
    elif not draft.selected_product_ids:
        st.info(
            "Requirements are complete. Select a recommended product next.",
            icon=":material/inventory_2:",
        )
    else:
        st.success(
            "Requirements are complete and a product is selected.",
            icon=":material/task_alt:",
        )


def _render_product_selection(
    state: QuotationWorkflowState,
    recommendation: QuoteRecommendation,
) -> None:
    items = _selectable_items(recommendation)
    if not items:
        st.info(
            "Add more product detail to obtain a catalog recommendation.",
            icon=":material/info:",
        )
        return

    main_model = recommendation.main_model
    if main_model:
        st.markdown(
            f"**Recommended**  \n{main_model.short_description}  \n"
            f"Product ID: `{main_model.product_id}`"
        )
        st.info(
            f"Why this product: {main_model.reason}",
            icon=":material/lightbulb:",
        )
    if recommendation.accessories:
        with st.expander(
            "Configured supporting components",
            expanded=False,
            icon=":material/handyman:",
        ):
            for item in recommendation.accessories:
                st.markdown(
                    f"- **{item.short_description}** (`{item.product_id}`)  \n"
                    f"  :gray[{item.reason}]"
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
    if st.button(
        "Use selected product",
        type="primary",
        icon=":material/check:",
    ):
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
            st.error(
                f"I could not select that product: {error}",
                icon=":material/error:",
            )
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
        _persist_and_rerun(
            state,
            event_type="product_selected",
            changed_fields=("selected_product_ids",),
        )


def _render_quotation_editor(
    state: QuotationWorkflowState,
    recommendation: QuoteRecommendation | None,
) -> None:
    with st.expander(
        "Edit quotation and revalidate",
        expanded=False,
        icon=":material/edit:",
    ):
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
            submitted = st.form_submit_button(
                "Save edits and require re-analysis",
                icon=":material/save:",
                use_container_width=True,
            )

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
                st.error(
                    f"The quotation could not be updated: {error}",
                    icon=":material/error:",
                )
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
                _persist_and_rerun(
                    state,
                    event_type="quotation_edited",
                    changed_fields=tuple(changed_fields),
                )
            st.info(
                "No quotation fields changed.",
                icon=":material/info:",
            )


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
        icon=":material/calculate:",
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
            st.error(
                f"Pricing analysis is unavailable: {error}",
                icon=":material/error:",
            )
            return
        _persist_and_rerun(
            state,
            event_type="pricing_completed",
            changed_fields=("pricing_result",),
        )

    result = state.pricing_result
    if result is None:
        return

    currency = result.currency or "USD"
    metrics = st.columns(4, gap="medium", border=True)
    metrics[0].metric("Selected product", state.draft.selected_product_ids[0])
    metrics[1].metric("Quantity", state.draft.quantity)
    metrics[2].metric(
        "Reference list price",
        _format_money(result.reference_list_price, currency),
    )
    metrics[3].metric("Confidence", result.confidence_label or "Low")

    price_metrics = st.columns(4, gap="medium", border=True)
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
        st.error(
            "Pricing is unavailable for the selected product.",
            icon=":material/error:",
        )

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
        with st.expander(
            "How the price was calculated",
            expanded=True,
            icon=":material/functions:",
        ):
            st.write(
                "The engine selects strong exact/description comparables, uses "
                "the configured median price hierarchy, applies the quantity "
                "adjustment, and then enforces available demo floors."
            )
            for assumption in result.assumptions:
                st.write(f"- {assumption}")
    for warning in result.warnings:
        st.warning(warning, icon=":material/warning:")

    if _internal_data_visible():
        with st.expander(
            "Internal analysis — restricted",
            expanded=False,
            icon=":material/lock:",
        ):
            st.metric(
                "Estimated unit cost",
                _format_money(result.estimated_cost, currency),
            )
            st.metric(
                "Gross margin amount",
                _format_money(result.gross_margin_amount, currency),
            )

    st.divider()
    st.subheader("E. Validation")
    if st.button(
        "Run technical and commercial validation",
        type="primary",
        icon=":material/rule:",
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
            st.error(
                f"Validation could not be completed: {error}",
                icon=":material/error:",
            )
            return
        _persist_and_rerun(
            state,
            event_type="validation_completed",
            changed_fields=("combined_decision",),
        )

    if state.validation_stale:
        st.info(
            "Validation is pending or stale. Run validation before proceeding.",
            icon=":material/hourglass_top:",
        )
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
    technical_icon = _status_icon(technical.status)
    with st.container(border=True):
        st.markdown(
            f"#### {technical_icon} Technical validation — "
            f"{technical.status.replace('_', ' ').upper()}"
        )
        check_groups = (
            ("Passed checks", technical.passed_checks, ":material/check_circle:"),
            ("Warnings", technical.warnings, ":material/warning:"),
            ("Errors", technical.errors, ":material/cancel:"),
            (
                "Not evaluated",
                technical.not_evaluated_checks,
                ":material/help:",
            ),
        )
        populated = [group for group in check_groups if group[1]]
        if populated:
            for tab, (title, entries, icon) in zip(
                st.tabs([f"{title} ({len(entries)})" for title, entries, _ in populated]),
                populated,
            ):
                with tab:
                    for entry in entries:
                        st.markdown(f"{icon} {entry}")

    st.markdown(
        f"#### {_status_icon(commercial.status)} Commercial validation — "
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
        with st.container(border=True):
            st.markdown(
                f"#### {_status_icon(decision.status)} Logical judgement — "
                f"{decision.status.replace('_', ' ').upper()}"
            )
            st.write(decision.summary)
            if decision.triggered_rule_ids:
                st.caption(
                    "Triggered rules: "
                    + ", ".join(decision.triggered_rule_ids)
                )
            st.info(
                f"Next action: {decision.recommended_next_action}",
                icon=":material/arrow_forward:",
            )
            if decision.approval_required:
                st.warning(
                    "Review is required before approval can be completed.",
                    icon=":material/gavel:",
                )
            elif decision.status == "blocked":
                st.error(
                    "This quotation is blocked. Correct the stated issues and "
                    "revalidate, or request revision.",
                    icon=":material/block:",
                )
            else:
                st.success(
                    "The quotation can proceed to human review.",
                    icon=":material/task_alt:",
                )


def _render_approval_panel(state: QuotationWorkflowState) -> None:
    approval = prepare_approval(state)
    st.divider()
    st.subheader("F. Human review")
    st.warning(
        "Demo-only simulated approval. The selected role is not authenticated "
        "and this action is not a company authorization.",
        icon=":material/policy:",
    )
    recommended_price = state.pricing_result.recommended_unit_price
    proposed_price = (
        state.draft.proposed_unit_price
        if state.draft.proposed_unit_price is not None
        else recommended_price
    )
    approval_metrics = st.columns(3, gap="medium", border=True)
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
        st.caption(
            "Review reasons: "
            + ", ".join(state.combined_decision.triggered_rule_ids)
        )

    if approval.status == ApprovalStatus.PENDING_REVIEW:
        st.info(
            approval_reminder_status(state),
            icon=":material/schedule:",
        )
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
                ACTION_APPROVE: ("Approve", ":material/check_circle:"),
                ACTION_APPROVE_WITH_OVERRIDE: (
                    "Approve with override",
                    ":material/published_with_changes:",
                ),
                ACTION_REQUEST_REVISION: (
                    "Request revision",
                    ":material/edit_note:",
                ),
                ACTION_REJECT: ("Reject", ":material/cancel:"),
            }
            action_columns = st.columns(len(actions) or 1, gap="small")
            for column, action in zip(action_columns, actions):
                label, icon = action_labels[action]
                if column.form_submit_button(
                    label,
                    icon=icon,
                    type="primary" if action == ACTION_APPROVE else "secondary",
                    use_container_width=True,
                ):
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
                st.error(
                    f"Approval action was not accepted: {error}",
                    icon=":material/error:",
                )
                return
            _persist_and_rerun(
                state,
                event_type=f"approval_{submitted_action}",
                changed_fields=("approval",),
            )
        return

    status_label = approval.status.value.replace("_", " ").upper()
    with st.container(border=True):
        st.markdown(f"#### {_status_icon(approval.status.value)} {status_label}")
        if approval.actor:
            st.markdown(f"**Actor**  \n:gray[{approval.actor} ({approval.actor_role})]")
        if approval.final_price is not None:
            st.markdown(
                "**Final approved unit price**  \n:gray["
                + _format_money(
                    approval.final_price,
                    state.pricing_result.currency,
                )
                + "]"
            )
        if approval.reason:
            st.markdown(f"**Reason**  \n:gray[{approval.reason}]")
        if approval.timestamp:
            st.caption(f"Recorded at {approval.timestamp.isoformat()}")


def _render_output_stage(state: QuotationWorkflowState) -> None:
    st.divider()
    st.subheader("G. Communication")
    status = state.approval.status
    try:
        previews: list[tuple[str, EmailOutput, str]] = []
        if status in APPROVED_STATUSES or status == ApprovalStatus.PENDING_REVIEW:
            internal_email = generate_internal_approval_email(state)
            state.internal_email = internal_email
            previews.append(("Internal approval", internal_email, "internal"))

        if status == ApprovalStatus.PENDING_REVIEW:
            previews.append(
                ("Internal reminder", generate_reminder_email(state), "reminder")
            )

        if status in {
            ApprovalStatus.REJECTED,
            ApprovalStatus.REVISION_REQUESTED,
        }:
            previews.append(
                (
                    "Rejection / revision",
                    generate_revision_email(state),
                    "revision",
                )
            )

        quotation_pdf = None
        if status in APPROVED_STATUSES:
            customer_email = generate_customer_email(state)
            quotation_pdf = generate_quotation_pdf(state)
            state.customer_email = customer_email
            previews.append(("Customer quotation", customer_email, "customer"))

        if previews:
            for tab, (title, email, preview_key) in zip(
                st.tabs([title for title, _, _ in previews]),
                previews,
            ):
                with tab:
                    _render_email_preview(email, state, preview_key)

        if status in APPROVED_STATUSES and quotation_pdf is not None:
            st.divider()
            st.subheader("H. Documents and audit")
            _render_customer_quotation_preview(state)
            st.download_button(
                "Download quotation PDF",
                data=quotation_pdf.bytes_data,
                file_name=quotation_pdf.filename,
                mime=quotation_pdf.mime_type,
                icon=":material/picture_as_pdf:",
                type="primary",
            )
            _render_audit_exports(state, include_customer=True)
        elif status in {
            ApprovalStatus.REJECTED,
            ApprovalStatus.REVISION_REQUESTED,
        }:
            st.divider()
            st.subheader("H. Documents and audit")
            st.info(
                "A customer PDF is unavailable because this quotation was not approved.",
                icon=":material/info:",
            )
            _render_audit_exports(state, include_customer=False)
    except OutputGenerationError as error:
        LOGGER.warning(
            "Output generation failed (%s).",
            type(error).__name__,
        )
        st.error(
            f"Outputs could not be generated: {error}",
            icon=":material/error:",
        )


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
        st.error(
            "Current pricing is required for the quotation preview.",
            icon=":material/error:",
        )
        return
    final_price = state.approval.final_price
    total = (
        final_price * state.draft.quantity
        if final_price is not None
        else None
    )
    st.markdown("#### Final quotation preview")
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
    st.markdown("#### Structured audit exports")
    columns = st.columns(2 if include_customer else 1, gap="medium")
    columns[0].download_button(
        "Download internal audit JSON",
        data=export_json_bytes(build_internal_audit_export(state)),
        file_name=f"{state.draft.quotation_id}-internal-audit.json",
        mime="application/json",
        icon=":material/description:",
        use_container_width=True,
    )
    if include_customer:
        columns[1].download_button(
            "Download customer quotation data JSON",
            data=export_json_bytes(build_customer_quotation_export(state)),
            file_name=f"{state.draft.quotation_id}-customer-data.json",
            mime="application/json",
            icon=":material/description:",
            use_container_width=True,
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
