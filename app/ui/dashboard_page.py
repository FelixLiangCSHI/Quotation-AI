"""Role-aware dashboard homepage.

Each role lands here and sees its own headline, metrics and quick actions, so
a sales user and an approver never open the same starting page. All figures
come from :mod:`app.ui.dashboard_data`, which only reads the existing service
layer.
"""

from __future__ import annotations

import streamlit as st

from app.auth.roles import Permission, role_label
from app.ui.dashboard_data import DashboardData, build_dashboard
from app.ui.navigation import landing_headline

__all__ = ["render"]

#: Quick actions offered per permission, in display order.
_QUICK_ACTIONS: tuple[tuple[Permission, str, str, str], ...] = (
    (
        Permission.CREATE_QUOTATION,
        "create_quotation",
        "New quotation",
        ":material/add_circle:",
    ),
    (
        Permission.VIEW_OWN_QUOTATIONS,
        "my_quotations",
        "My quotations",
        ":material/folder_open:",
    ),
    (
        Permission.VIEW_APPROVAL_TASKS,
        "approval_center",
        "Approval center",
        ":material/approval:",
    ),
    (
        Permission.MANAGE_DATA_VERSIONS,
        "pricing_data",
        "Pricing data",
        ":material/table_chart:",
    ),
    (
        Permission.MANAGE_USERS,
        "users",
        "User management",
        ":material/group:",
    ),
    (
        Permission.VIEW_AUDIT_RECORDS,
        "audit",
        "Audit trail",
        ":material/history:",
    ),
)


def render(user, *, navigate=None) -> None:
    """Render the dashboard. ``navigate`` switches the shell to another page."""

    st.title(f":material/dashboard: {landing_headline(user)}")
    _render_identity_row(user)

    try:
        data = build_dashboard(user)
    except Exception:  # noqa: BLE001 - a dashboard must never block sign-in
        st.error(
            "The dashboard could not be loaded. Use the sidebar to open a "
            "workspace directly.",
            icon=":material/error:",
        )
        return

    for notice in data.notices:
        st.caption(notice)

    _render_metrics(user, data)
    st.divider()
    _render_quick_actions(user, navigate)
    st.divider()

    left, right = st.columns(2, gap="large")
    with left:
        _render_active_quotations(user, data)
    with right:
        _render_pending_tasks(user, data)

    st.divider()
    _render_recent_activity(data)


def _render_identity_row(user) -> None:
    columns = st.columns([2, 2, 3], gap="medium")
    columns[0].markdown(f"**{user.display_name or user.username}**")
    columns[0].caption(user.username)
    columns[1].markdown(
        "**" + ", ".join(role_label(role) for role in user.roles) + "**"
    )
    columns[1].caption("Assigned by an administrator")
    columns[2].caption(
        "Internal view: cost, margin, thresholds and approval rules are "
        "visible here and are excluded from every customer document."
    )


def _render_metrics(user, data: DashboardData) -> None:
    tiles = st.columns(3, gap="medium", border=True)
    if user.has_permission(Permission.VIEW_OWN_QUOTATIONS):
        tiles[0].metric("Active quotations", data.active_quotation_count)
    else:
        tiles[0].metric("Active quotations", "—")
    if user.has_permission(Permission.VIEW_APPROVAL_TASKS):
        tiles[1].metric("Pending tasks", data.pending_task_count)
        tiles[2].metric("Decisions recorded", len(data.completed_tasks))
    else:
        tiles[1].metric("Pending tasks", "—")
        tiles[2].metric("Quotations owned", len(data.my_quotations))


def _render_quick_actions(user, navigate) -> None:
    st.markdown("#### Quick actions")
    available = [
        (key, label, icon)
        for permission, key, label, icon in _QUICK_ACTIONS
        if user.has_permission(permission)
    ]
    if not available:
        st.caption("No quick action is available for your role.")
        return
    columns = st.columns(len(available), gap="small")
    for column, (key, label, icon) in zip(columns, available):
        if column.button(
            label,
            icon=icon,
            use_container_width=True,
            key=f"quick_action_{key}",
        ):
            if navigate is not None:
                navigate(key)


def _render_active_quotations(user, data: DashboardData) -> None:
    st.markdown("#### Active quotations")
    if not user.has_permission(Permission.VIEW_OWN_QUOTATIONS):
        st.caption("Your role does not include quotation visibility.")
        return
    if not data.active_quotations:
        st.info(
            "No quotation is in progress. Use **New quotation** to start one.",
            icon=":material/note_add:",
        )
        return
    st.dataframe(
        [
            {
                "quotation": item.quotation_id,
                "customer": item.customer_name or "—",
                "status": item.status,
                "approval": item.approval_status,
                "version": item.version,
                "updated": item.updated_at,
            }
            for item in data.active_quotations[:10]
        ],
        use_container_width=True,
        hide_index=True,
    )


def _render_pending_tasks(user, data: DashboardData) -> None:
    st.markdown("#### Pending tasks")
    if not user.has_permission(Permission.VIEW_APPROVAL_TASKS):
        st.caption(
            "Approval tasks are visible to approvers. Submit a quotation to "
            "send it for review."
        )
        return
    if not data.pending_tasks:
        st.info(
            "Nothing is waiting for your decision.", icon=":material/inbox:"
        )
        return
    st.dataframe(
        [
            {
                "task": task.task_reference,
                "quotation": task.quotation_reference,
                "version": task.quotation_version,
                "decision": task.decision_status,
                "submitted": task.submitted_at,
                "reminder due": task.reminder_due_at,
            }
            for task in data.pending_tasks[:10]
        ],
        use_container_width=True,
        hide_index=True,
    )


def _render_recent_activity(data: DashboardData) -> None:
    st.markdown("#### Recent activity")
    if not data.recent_events:
        st.info(
            "No activity has been recorded yet.",
            icon=":material/history_toggle_off:",
        )
        return
    st.dataframe(
        [
            {
                "when": event.occurred_at,
                "event": event.event_type,
                "quotation": event.quotation_reference or "—",
                "actor": event.actor or "—",
                "result": event.after_state or "—",
            }
            for event in data.recent_events
        ],
        use_container_width=True,
        hide_index=True,
    )
