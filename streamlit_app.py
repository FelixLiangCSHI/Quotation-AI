"""Application shell: authentication gate, sidebar and role-based routing.

The shell owns everything that must be true on every page: configuration is
promoted from secrets, the schema exists, a user is signed in, the session has
not timed out, and the sidebar lists only the workspaces the signed-in role may
open. Individual pages are pure renderers that receive the authenticated
principal.
"""

from __future__ import annotations

import logging

import streamlit as st

from app.runtime import APP_VERSION, bootstrap_from_streamlit

# Streamlit Cloud supplies configuration through secrets, so they must be
# promoted to environment variables before any configuration module is
# imported. Missing secrets are normal and leave the deterministic defaults.
bootstrap_from_streamlit()

from app.auth.demo_accounts import seed_demo_accounts  # noqa: E402
from app.auth.roles import role_label  # noqa: E402
from app.config import DEMO_MODE  # noqa: E402
from app.services.auth_session import current_user, sign_out  # noqa: E402
from app.services.workflow_session import ensure_schema  # noqa: E402
from app.ui import (  # noqa: E402
    admin_pages,
    approval_page,
    audit_page,
    dashboard_page,
    documents_page,
    email_page,
    login_page,
    my_quotations_page,
    pricing_data_page,
    quotation_workspace,
)
from app.ui.navigation import (  # noqa: E402
    default_page_key,
    pages_for,
    workspace_label,
)
from app.ui.session_guard import evaluate_session  # noqa: E402

LOGGER = logging.getLogger(__name__)

#: Session key holding the workspace the user is currently viewing.
CURRENT_PAGE_KEY = "ui_current_page"


def _configure_page() -> None:
    st.set_page_config(
        page_title="Quotation Workspace",
        page_icon=":material/request_quote:",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "About": (
                "Internal quotation workspace — a deterministic, rule-backed "
                "quotation, approval and audit tool."
            )
        },
    )


def navigate(page_key: str) -> None:
    """Switch the shell to another workspace."""

    st.session_state[CURRENT_PAGE_KEY] = page_key


def _resolve_current_page(user) -> str:
    allowed = {entry.key for entry in pages_for(user)}
    current = st.session_state.get(CURRENT_PAGE_KEY)
    if current not in allowed:
        current = default_page_key(user)
        st.session_state[CURRENT_PAGE_KEY] = current
    return current


def _render_sidebar(user, current: str) -> None:
    with st.sidebar:
        st.markdown(f"### {user.display_name or user.username}")
        st.caption(", ".join(role_label(role) for role in user.roles))
        if DEMO_MODE:
            st.caption("Demo mode — synthetic data only")
        st.divider()

        last_section = ""
        for entry in pages_for(user):
            if entry.section != last_section:
                st.caption(entry.section.upper())
                last_section = entry.section
            if st.button(
                entry.label,
                icon=entry.icon,
                key=f"nav_{entry.key}",
                use_container_width=True,
                type="primary" if entry.key == current else "secondary",
            ):
                navigate(entry.key)
                st.rerun()

        st.divider()
        if st.button(
            "Sign out",
            icon=":material/logout:",
            use_container_width=True,
        ):
            sign_out(st.session_state)
            st.session_state.pop(CURRENT_PAGE_KEY, None)
            st.rerun()
        st.caption(f"Version {APP_VERSION}")


def _render_page(page_key: str, user) -> None:
    if page_key == "dashboard":
        dashboard_page.render(user, navigate=navigate)
    elif page_key == "create_quotation":
        quotation_workspace.render(user)
    elif page_key == "my_quotations":
        my_quotations_page.render(user, navigate=navigate)
    elif page_key == "approval_center":
        approval_page.render(user)
    elif page_key == "approval_history":
        approval_page.render_history(user)
    elif page_key == "pricing_data":
        pricing_data_page.render(user)
    elif page_key == "policy":
        admin_pages.render_policy(user)
    elif page_key == "users":
        admin_pages.render_users(user)
    elif page_key == "system":
        admin_pages.render_system(user)
    elif page_key == "documents":
        documents_page.render(user)
    elif page_key == "email":
        email_page.render(user)
    elif page_key == "audit":
        audit_page.render(user)
    else:
        st.error(
            f"Unknown workspace: {workspace_label(page_key)}",
            icon=":material/error:",
        )


@st.cache_resource(show_spinner=False)
def _seed_demo_accounts_once() -> bool:
    """Create the demo accounts of a demo deployment, at most once per process.

    Seeding is idempotent and never changes an existing account, so a real
    deployment that already manages its users is unaffected.
    """

    try:
        seed_demo_accounts()
    except Exception as error:  # noqa: BLE001 - startup must never fail here
        LOGGER.warning(
            "Demo account seeding skipped (%s).", type(error).__name__
        )
        return False
    return True


def main() -> None:
    _configure_page()
    ensure_schema()
    if DEMO_MODE:
        _seed_demo_accounts_once()

    status = evaluate_session(
        st.session_state,
        resolve=current_user,
        end_session=sign_out,
    )
    if not status.is_authenticated:
        # No business workflow is reachable before this point.
        st.session_state.pop(CURRENT_PAGE_KEY, None)
        login_page.render()
        return

    user = status.user
    current = _resolve_current_page(user)
    _render_sidebar(user, current)
    _render_page(current, user)


def run() -> None:
    """Entry point with an error boundary so the app degrades, not crashes."""

    try:
        main()
    except st.errors.Error:
        raise
    except Exception as error:  # noqa: BLE001 - the app must not crash
        LOGGER.exception(
            "Unhandled application error (%s).", type(error).__name__
        )
        st.error(
            "The application hit an unexpected problem and stopped rendering "
            "this page. Reload the page or choose another workspace.",
            icon=":material/error:",
        )
        st.caption(f"Error type: {type(error).__name__}")


run()
