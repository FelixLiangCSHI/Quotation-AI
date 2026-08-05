"""Email centre — drafts, delivery status and reminder state.

This page only *displays* persisted email and reminder state and offers the
actions the service layer already permits. No scheduler runs here: reminder
due times live in the database and are processed by
``python -m worker.reminder_worker``, so closing or restarting the web process
cannot lose a pending reminder.
"""

from __future__ import annotations

import streamlit as st

from app.auth.roles import Permission, role_label
from app.emailing.contracts import EmailError, EmailStatus, EmailType
from app.emailing.service import EmailService
from app.services.approval_service import ApprovalService
from app.services.auth_session import current_user, sign_in, sign_out
from app.services.workflow_session import ensure_schema

st.set_page_config(page_title="Email centre", layout="wide")

STATUS_ICONS = {
    EmailStatus.SENT.value: ":material/mark_email_read:",
    EmailStatus.FAILED.value: ":material/error:",
    EmailStatus.PENDING_REVIEW.value: ":material/rate_review:",
    EmailStatus.DRAFTED.value: ":material/drafts:",
    EmailStatus.QUEUED.value: ":material/schedule_send:",
}


def _render_sign_in() -> None:
    st.title(":material/lock: Sign in")
    st.caption("Email delivery is an authenticated internal action.")
    with st.form("email_sign_in"):
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
        st.caption("Roles: " + ", ".join(role_label(role) for role in user.roles))
        if st.button("Sign out", icon=":material/logout:"):
            sign_out(st.session_state)
            st.rerun()


def _render_configuration(emails: EmailService) -> None:
    with st.expander("Email configuration", icon=":material/settings:"):
        st.caption(
            "Every setting comes from an environment variable. Secret values "
            "are never read into this page — only the variable names and "
            "whether they are present."
        )
        st.json(emails.describe_configuration())


def _render_reminder_state(task) -> None:
    columns = st.columns(4, gap="medium")
    columns[0].metric("Task status", task.status.replace("_", " "))
    columns[1].metric(
        "Reminder due",
        "not scheduled"
        if task.reminder_due_at is None
        else task.reminder_due_at.isoformat(timespec="minutes"),
    )
    columns[2].metric("Reminders sent", task.reminder_sent_count)
    columns[3].metric(
        "Last reminder",
        "never"
        if task.reminder_last_sent_at is None
        else task.reminder_last_sent_at.isoformat(timespec="minutes"),
    )
    if task.reminder_last_error_category not in ("", "none"):
        st.warning(
            "Last reminder attempt failed: "
            f"{task.reminder_last_error_category}.",
            icon=":material/report:",
        )


def _render_email(emails: EmailService, user, record) -> None:
    icon = STATUS_ICONS.get(record.status, ":material/mail:")
    st.markdown(
        f"{icon} **{record.email_type.replace('_', ' ')}** — "
        f"{record.status.replace('_', ' ')}"
    )
    st.caption(
        f"v{record.quotation_version} · to {', '.join(record.recipients)} · "
        f"provider {record.delivery_provider} · attempts {record.attempt_count}"
    )
    st.write(record.subject)
    if record.body:
        with st.expander("Stored body", icon=":material/description:"):
            st.text(record.body)
    else:
        st.caption(
            "The body is not persisted under the configured storage policy; "
            f"template {record.template_version}, body hash "
            f"{record.body_hash[:12]}…"
        )
    if record.agent_fallback_used:
        st.caption(
            "Wording: deterministic template"
            + (
                f" (Agent 3 output discarded: {record.agent_fallback_reason})"
                if record.agent_fallback_reason
                else ""
            )
        )
    else:
        st.caption(f"Wording: reviewed Agent 3 draft via {record.agent_provider}")

    if record.status == EmailStatus.FAILED.value:
        st.error(
            f"Delivery failed: {record.last_error_category}.",
            icon=":material/error:",
        )
        retryable = record.email_type != EmailType.CUSTOMER_QUOTATION.value
        if retryable and user.has_permission(Permission.SUBMIT_QUOTATION):
            if st.button(
                "Retry delivery",
                key=f"retry-{record.id}",
                icon=":material/refresh:",
            ):
                try:
                    emails.retry_delivery(record.id, user=user)
                except EmailError as error:
                    st.error(str(error), icon=":material/block:")
                else:
                    st.rerun()
        elif not retryable:
            st.caption(
                "A customer email is re-sent only through the draft review "
                "step, never by a blind retry."
            )


def main() -> None:
    ensure_schema()
    user = current_user(st.session_state)
    if user is None:
        _render_sign_in()
        return

    _render_identity(user)
    st.title(":material/outgoing_mail: Email centre")

    if not user.has_permission(Permission.VIEW_OWN_QUOTATIONS):
        st.error("Your role does not include quotation access.", icon=":material/block:")
        return

    emails = EmailService()
    _render_configuration(emails)
    st.info(
        "Reminders are sent by the separate worker process "
        "(`python -m worker.reminder_worker --run-once`). This page never "
        "schedules or sends a reminder itself.",
        icon=":material/schedule:",
    )

    quotation_id = st.text_input("Quotation ID", key="email_centre_quotation")
    if not quotation_id.strip():
        st.caption("Enter a quotation reference to review its emails.")
        return

    if user.has_permission(Permission.VIEW_APPROVAL_TASKS):
        approvals = ApprovalService()
        tasks = approvals.list_tasks(user, only_open=False, assigned_to_me=False)
        for task in tasks:
            if task.quotation_reference != quotation_id.strip():
                continue
            with st.container(border=True):
                st.subheader(f"Approval task {task.id}")
                _render_reminder_state(task)

    records = emails.list_emails(quotation_id.strip(), user=user)
    if not records:
        st.info("No email has been generated for this quotation yet.")
        return
    for record in records:
        with st.container(border=True):
            _render_email(emails, user, record)


main()
