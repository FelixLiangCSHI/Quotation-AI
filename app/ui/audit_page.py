"""Internal audit trail — role-restricted read-only view."""

from __future__ import annotations

import streamlit as st

from app.auth.provider import PermissionDeniedError
from app.auth.roles import Permission
from app.services.audit_view import AuditViewService


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


def render(user) -> None:
    """Render the internal audit trail for an authenticated user."""

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
        st.info(
            "No audit record matches this filter. Records appear as soon as a "
            "quotation is created, submitted or decided.",
            icon=":material/search_off:",
        )
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

