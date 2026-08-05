"""Login page — the single entry point into the application.

No workflow page is reachable before this form succeeds. The password is
submitted straight to the authentication provider and is never written to
Streamlit session state, logged or echoed back.
"""

from __future__ import annotations

import streamlit as st

from app.auth.demo_accounts import DEMO_ACCOUNTS
from app.auth.local_provider import AccountLockedError
from app.auth.provider import AuthenticationError
from app.auth.roles import role_label
from app.config import DEMO_MODE
from app.services.auth_session import sign_in
from app.ui.session_guard import TIMEOUT_NOTICE_KEY, touch

__all__ = ["render"]

#: Session key holding the username pre-filled from the demo-role dropdown.
DEMO_USERNAME_KEY = "login_username"
_MANUAL_CHOICE = "Sign in with my own account"


def _demo_choices() -> dict[str, str]:
    """Map a human role label to the matching demo username."""

    return {
        f"{role_label(role)} — {username}": username
        for username, role, _ in DEMO_ACCOUNTS
    }


def _render_demo_role_picker() -> None:
    """Let a presenter jump between roles without memorising usernames.

    Only the username is filled in; the password is always typed, so no
    credential is ever stored in the page or in session state.
    """

    choices = _demo_choices()
    options = [_MANUAL_CHOICE, *choices]
    selection = st.selectbox(
        "Demo role",
        options=options,
        help=(
            "Pick a role to pre-fill its demo username. The password is still "
            "required."
        ),
    )
    if selection == _MANUAL_CHOICE:
        return
    username = choices[selection]
    if st.session_state.get(DEMO_USERNAME_KEY) != username:
        st.session_state[DEMO_USERNAME_KEY] = username
        st.rerun()


def render() -> None:
    st.title(":material/lock: Sign in")
    st.caption(
        "Internal quotation workspace. Access, roles and every approval "
        "action are granted by an administrator and can never be chosen here."
    )

    if st.session_state.pop(TIMEOUT_NOTICE_KEY, False):
        st.warning(
            "Your session ended after a period of inactivity. Sign in again "
            "to continue.",
            icon=":material/timer_off:",
        )

    left, right = st.columns([3, 2], gap="large")
    with left:
        if DEMO_MODE:
            _render_demo_role_picker()
        with st.form("sign_in"):
            username = st.text_input("Username", key=DEMO_USERNAME_KEY)
            secret = st.text_input("Password", type="password")
            submitted = st.form_submit_button(
                "Sign in", icon=":material/login:", type="primary"
            )
        if submitted:
            _attempt_sign_in(username, secret)
    with right:
        with st.container(border=True):
            st.markdown("#### What you can do after signing in")
            st.caption(
                "Sales user — create quotations, track your own drafts and "
                "submit them for approval."
            )
            st.caption(
                "Sales manager — review, approve, request revisions or "
                "reject the quotations assigned to you."
            )
            st.caption(
                "Pricing manager — approve with commercial detail and manage "
                "policy versions."
            )
            st.caption(
                "Administrator — manage users, pricing data versions, system "
                "configuration and the audit trail."
            )


def _attempt_sign_in(username: str, secret: str) -> None:
    if not username.strip() or not secret:
        st.error(
            "Enter both a username and a password.", icon=":material/error:"
        )
        return
    try:
        sign_in(st.session_state, username=username, password=secret)
    except AccountLockedError as error:
        st.error(str(error), icon=":material/lock_clock:")
        return
    except AuthenticationError as error:
        st.error(str(error), icon=":material/error:")
        return
    except Exception:  # noqa: BLE001 - never leak an internal failure
        st.error(
            "Sign in is temporarily unavailable. Try again in a moment.",
            icon=":material/error:",
        )
        return
    touch(st.session_state)
    st.rerun()
