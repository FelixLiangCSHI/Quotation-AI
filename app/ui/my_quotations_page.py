"""My quotations — the sales user's own list of drafts and submissions."""

from __future__ import annotations

import streamlit as st

from app.auth.roles import Permission
from app.repositories.interfaces import QuotationNotFoundError
from app.services.quotation_service import QuotationService
from app.services.workflow_session import open_quotation

__all__ = ["render"]


def render(user, *, navigate=None) -> None:
    st.title(":material/folder_open: My Quotations")

    if not user.has_permission(Permission.VIEW_OWN_QUOTATIONS):
        st.error(
            "Your role does not include quotation access.",
            icon=":material/block:",
        )
        return

    service = QuotationService()
    # An approver or administrator may see every quotation their role already
    # permits; a sales user sees only their own.
    owner = (
        None
        if user.has_permission(Permission.VIEW_APPROVAL_TASKS)
        else user.user_id
    )
    include_closed = st.toggle("Include closed quotations", value=False)
    try:
        summaries = service.list_quotations(
            owner_user_id=owner, include_closed=include_closed
        )
    except Exception:  # noqa: BLE001 - a listing failure must not crash the app
        st.error(
            "The quotation list could not be loaded. Try again in a moment.",
            icon=":material/error:",
        )
        return

    if not summaries:
        st.info(
            "You have no quotation yet. Open **Create Quotation** to start "
            "one.",
            icon=":material/note_add:",
        )
        return

    ordered = sorted(summaries, key=lambda item: item.updated_at, reverse=True)
    st.caption(f"{len(ordered)} quotation(s).")
    for summary in ordered:
        with st.container(border=True):
            header, action = st.columns([5, 1], gap="medium")
            header.markdown(
                f"**{summary.quotation_id}** — "
                f"{summary.customer_name or 'Unnamed customer'}"
            )
            header.caption(
                f"Status: {summary.status} · Approval: "
                f"{summary.approval_status} · Version {summary.version} · "
                f"Updated {summary.updated_at:%Y-%m-%d %H:%M}"
            )
            if action.button(
                "Open",
                icon=":material/open_in_new:",
                key=f"open_quotation_{summary.quotation_id}",
                use_container_width=True,
            ):
                _open(summary.quotation_id, navigate)


def _open(quotation_id: str, navigate) -> None:
    try:
        open_quotation(st.session_state, quotation_id)
    except QuotationNotFoundError:
        st.error(
            "That quotation no longer exists.", icon=":material/search_off:"
        )
        return
    if navigate is not None:
        navigate("create_quotation")
    st.rerun()
