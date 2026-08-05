"""Internal audit trail — role-restricted read-only view."""

from __future__ import annotations

import streamlit as st

from app.auth.provider import PermissionDeniedError
from app.auth.roles import Permission, role_label
from app.services.audit_view import AuditViewService
from app.services.auth_session import current_user, sign_in, sign_out
from app.services.workflow_session import ensure_schema

st.set_page_config(page_title="Audit trail", layout="wide")


def _render_sign_in() -> None:
    st.title(":material/lock: Sign in")
    with st.form("audit_sign_in"):
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


def _row(event) -> dict[str, object]:
    return {
        "timestamp": event.occurred_at,
        "event": event.event_type,
        "actor": event.actor,
        "actor role": event.actor_role,
        "quotation": event.quotation_reference,
        "version": event.quotation_version,
        "before": event.before_state,
        "after": event.after_state,
        "changed fields": ", ".join(event.changed_fields),
        "policy version": event.policy_version_id,
        "rules": ", ".join(event.triggered_rule_ids),
        "request id": event.request_id,
        "reason": event.reason,
    }


def main() -> None:
    ensure_schema()
    user = current_user(st.session_state)
    if user is None:
        _render_sign_in()
        return

    with st.sidebar:
        st.markdown(f"**{user.display_name or user.username}**")
        st.caption(
            "Roles: " + ", ".join(role_label(role) for role in user.roles)
        )
        if st.button("Sign out", icon=":material/logout:"):
            sign_out(st.session_state)
            st.rerun()

    st.title(":material/history: Internal audit trail")
    if not user.has_permission(Permission.VIEW_AUDIT_RECORDS):
        st.error(
            "Your role does not include access to audit records.",
            icon=":material/block:",
        )
        return

    service = AuditViewService()
    quotation_id = st.text_input(
        "Filter by quotation id", placeholder="Leave empty for recent events"
    ).strip()
    try:
        events = (
            service.list_for_quotation(user, quotation_id)
            if quotation_id
            else service.list_recent(user, limit=200)
        )
    except PermissionDeniedError as error:
        st.error(str(error), icon=":material/block:")
        return

    if not events:
        st.info("No audit records match this filter.")
        return

    st.caption(
        "Secrets are never stored in audit records and credential-like values "
        "are redacted before display."
    )
    st.dataframe(
        [_row(event) for event in events],
        use_container_width=True,
        hide_index=True,
    )


main()
