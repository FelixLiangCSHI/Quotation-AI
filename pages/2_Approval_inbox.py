"""Approval inbox — the authenticated internal review page.

Everything shown here comes from the service layer. The action buttons render
exactly the set the backend already permits for this task and this user, so no
action can be triggered from the UI that the domain layer would refuse.
"""

from __future__ import annotations

import streamlit as st

from app.approval_workflow import (
    ACTION_APPROVE,
    ACTION_APPROVE_WITH_OVERRIDE,
    ACTION_REJECT,
    ACTION_REQUEST_REVISION,
    ApprovalWorkflowError,
)
from app.auth.provider import PermissionDeniedError
from app.auth.roles import Permission, role_label
from app.services.approval_service import (
    ApprovalService,
    ApprovalServiceError,
    ApprovalTaskView,
)
from app.services.auth_session import current_user, sign_in, sign_out
from app.services.workflow_session import ensure_schema

st.set_page_config(page_title="Approval inbox", layout="wide")

ACTION_LABELS = {
    ACTION_APPROVE: ("Approve", ":material/check_circle:"),
    ACTION_APPROVE_WITH_OVERRIDE: (
        "Approve with override",
        ":material/published_with_changes:",
    ),
    ACTION_REQUEST_REVISION: ("Request revision", ":material/edit_note:"),
    ACTION_REJECT: ("Reject", ":material/cancel:"),
}

MANDATORY_REASON_ACTIONS = (
    ACTION_APPROVE_WITH_OVERRIDE,
    ACTION_REQUEST_REVISION,
    ACTION_REJECT,
)


def _render_sign_in() -> None:
    st.title(":material/lock: Sign in")
    st.caption(
        "Approval is an authenticated internal action. Roles are assigned by "
        "an administrator and can never be chosen here."
    )
    with st.form("approval_sign_in"):
        username = st.text_input("Username")
        secret = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", icon=":material/login:")
    if not submitted:
        return
    try:
        sign_in(st.session_state, username=username, password=secret)
    except Exception as error:  # noqa: BLE001 - shown to the operator
        st.error(f"Sign in failed: {error}", icon=":material/error:")
        return
    st.rerun()


def _render_identity(user) -> None:
    with st.sidebar:
        st.markdown(f"**{user.display_name or user.username}**")
        st.caption(
            "Roles: " + ", ".join(role_label(role) for role in user.roles)
        )
        if st.button("Sign out", icon=":material/logout:"):
            sign_out(st.session_state)
            st.rerun()


def _render_task(service: ApprovalService, user, view: ApprovalTaskView) -> None:
    st.subheader(
        f"{view.quotation_id} — v{view.quotation_version} — "
        f"{view.customer_name or 'Unnamed customer'}"
    )
    header = st.columns(4, gap="medium", border=True)
    header[0].metric("Decision", view.decision_status.replace("_", " ").upper())
    header[1].metric("Gross margin", view.gross_margin_percent or "unavailable")
    header[2].metric("Policy threshold", view.threshold_percent or "unknown")
    header[3].metric(
        "Total revenue", view.total_revenue or "unavailable"
    )

    detail = st.columns(3, gap="medium")
    detail[0].caption(f"Quotation owner: {view.owner_username or 'unknown'}")
    detail[1].caption(
        "Total cost: "
        + (
            view.total_cost
            if view.total_cost is not None
            else "not visible for your role"
        )
    )
    detail[2].caption(f"Policy version: {view.policy_version_id or 'missing'}")

    st.caption(
        "Technical validation: "
        + (view.technical_validation_status or "not run")
    )
    if view.data_quality_flags:
        st.warning(
            "Data quality flags: " + ", ".join(view.data_quality_flags),
            icon=":material/report:",
        )
    if view.triggered_rule_ids:
        st.caption("Triggered rules: " + ", ".join(view.triggered_rule_ids))

    if view.line_items:
        st.dataframe(
            list(view.line_items), use_container_width=True, hide_index=True
        )

    if view.ai_explanation:
        st.info(
            f"{view.ai_explanation_label}\n\n{view.ai_explanation}",
            icon=":material/smart_toy:",
        )
        st.caption(
            "The AI-generated explanation is non-authoritative and is never "
            "the basis of the decision."
        )

    if view.is_stale:
        st.error(
            "This task is no longer current ("
            + ", ".join(view.stale_reasons)
            + "). The quotation must be repriced, revalidated and resubmitted.",
            icon=":material/sync_problem:",
        )
        return
    if not view.allowed_actions:
        st.info("No action is available on this task.", icon=":material/info:")
        return

    with st.form(f"approval_action_{view.task.id}"):
        reason = st.text_area(
            "Reason / justification",
            help=(
                "Mandatory for override approval, revision requests and "
                "rejections."
            ),
        )
        acknowledge = False
        if ACTION_APPROVE_WITH_OVERRIDE in view.allowed_actions:
            acknowledge = st.checkbox(
                "I acknowledge the quotation margin is equal to or below the "
                "configured policy threshold."
            )
        chosen = None
        columns = st.columns(len(view.allowed_actions), gap="small")
        for column, action in zip(columns, view.allowed_actions):
            label, icon = ACTION_LABELS[action]
            if column.form_submit_button(
                label,
                icon=icon,
                type="primary" if action == ACTION_APPROVE else "secondary",
                use_container_width=True,
            ):
                chosen = action

    if chosen is None:
        return
    try:
        service.act(
            user=user,
            task_id=view.task.id,
            action=chosen,
            reason=reason,
            acknowledge_below_threshold=acknowledge,
        )
    except (
        ApprovalServiceError,
        ApprovalWorkflowError,
        PermissionDeniedError,
    ) as error:
        st.error(f"Action refused: {error}", icon=":material/error:")
        return
    st.success("The approval action was recorded.", icon=":material/task_alt:")
    st.rerun()


def main() -> None:
    ensure_schema()
    user = current_user(st.session_state)
    if user is None:
        _render_sign_in()
        return

    _render_identity(user)
    st.title(":material/approval: Approval inbox")

    if not user.has_permission(Permission.VIEW_APPROVAL_TASKS):
        st.error(
            "Your role does not include approval review.",
            icon=":material/block:",
        )
        return

    service = ApprovalService()
    tasks = service.list_tasks(user)
    if not tasks:
        st.info("There are no pending approval tasks assigned to you.")
        return

    for task in tasks:
        with st.container(border=True):
            try:
                view = service.get_task_view(user, task.id)
            except ApprovalServiceError as error:
                st.error(str(error), icon=":material/error:")
                continue
            _render_task(service, user, view)


main()
