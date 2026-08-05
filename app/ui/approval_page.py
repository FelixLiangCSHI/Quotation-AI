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
from app.auth.roles import Permission
from app.services.approval_service import (
    ApprovalService,
    ApprovalServiceError,
    ApprovalTaskView,
)

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


def render(user) -> None:
    """Render the approval inbox for an already authenticated user."""

    st.title(":material/approval: Approval Center")
    if not user.has_permission(Permission.VIEW_APPROVAL_TASKS):
        st.error(
            "Your role does not include approval review.",
            icon=":material/block:",
        )
        return

    service = ApprovalService()
    try:
        tasks = service.list_tasks(user)
    except PermissionDeniedError as error:
        st.error(str(error), icon=":material/block:")
        return
    if not tasks:
        st.info(
            "No approval task is waiting for you. Tasks appear here as soon "
            "as a sales user submits a quotation that needs your decision.",
            icon=":material/inbox:",
        )
        return

    st.caption(
        f"{len(tasks)} task(s) awaiting your decision. Cost, margin and "
        "threshold shown below are internal information and never reach the "
        "customer document."
    )

    for task in tasks:
        with st.container(border=True):
            try:
                view = service.get_task_view(user, task.id)
            except ApprovalServiceError as error:
                st.error(str(error), icon=":material/error:")
                continue
            _render_task(service, user, view)


def render_history(user) -> None:
    """Render the approver's completed decisions."""

    st.title(":material/fact_check: Approval History")
    if not user.has_permission(Permission.VIEW_APPROVAL_TASKS):
        st.error(
            "Your role does not include approval review.",
            icon=":material/block:",
        )
        return

    service = ApprovalService()
    try:
        tasks = service.list_tasks(user, only_open=False, assigned_to_me=False)
    except PermissionDeniedError as error:
        st.error(str(error), icon=":material/block:")
        return
    completed = [task for task in tasks if task.completed_at is not None]
    if not completed:
        st.info(
            "No approval decision has been recorded yet.",
            icon=":material/history_toggle_off:",
        )
        return

    st.dataframe(
        [
            {
                "task": task.task_reference,
                "quotation": task.quotation_reference,
                "version": task.quotation_version,
                "decision": task.decision or task.status,
                "approver": task.assigned_approver_name,
                "role": task.assigned_approver_role,
                "completed": task.completed_at,
                "reason": task.reason,
            }
            for task in completed
        ],
        use_container_width=True,
        hide_index=True,
    )
