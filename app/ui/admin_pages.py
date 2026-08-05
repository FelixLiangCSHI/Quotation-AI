"""Administration pages: policy, users and system configuration.

Every page here is read-only over the existing configuration and repositories.
No business rule is defined in this module: commercial policy versions are
declared in :mod:`app.commercial_policy` and users are created through the
authentication provider, both of which remain the single sources of truth.
"""

from __future__ import annotations

import streamlit as st

from app.auth.provider import AuthenticationError
from app.auth.local_provider import LocalPasswordAuthenticationProvider
from app.auth.roles import Permission, Role, UnknownRoleError, role_label
from app.commercial_policy import DEFAULT_POLICY_REGISTRY
from app.config import DEMO_MODE, PRICING_DATA_MODE, SHOW_INTERNAL_COSTS
from app.runtime import APP_VERSION, runtime_report
from app.services.unit_of_work import UnitOfWork

__all__ = ["render_policy", "render_system", "render_users"]


def render_policy(user) -> None:
    st.title(":material/rule: Policy management")
    if not user.has_permission(Permission.MANAGE_POLICY_VERSIONS):
        st.error(
            "Your role does not include policy management.",
            icon=":material/block:",
        )
        return

    st.caption(
        "Commercial policy versions are immutable and auditable. A recorded "
        "decision always keeps the policy version it was evaluated against."
    )
    policies = DEFAULT_POLICY_REGISTRY.policies
    if not policies:
        st.info(
            "No commercial policy version is registered.",
            icon=":material/rule_folder:",
        )
        return
    st.dataframe(
        [
            {
                "policy": policy.policy_id,
                "name": policy.policy_name,
                "version": policy.version,
                "status": policy.status.value,
                "effective from": policy.effective_from,
                "effective to": policy.effective_to or "—",
                "pass threshold %": str(
                    policy.pass_margin_threshold_percent
                ),
                "missing cost": policy.missing_cost_policy.value,
                "currency": policy.currency_policy.value,
            }
            for policy in policies
        ],
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "Threshold and approval rules are internal information and are never "
        "included in a customer document."
    )


def render_users(user) -> None:
    st.title(":material/group: User management")
    if not user.has_permission(Permission.MANAGE_USERS):
        st.error(
            "Your role does not include user management.",
            icon=":material/block:",
        )
        return

    with UnitOfWork() as uow:
        accounts = uow.users.list_users(only_active=False)

    if not accounts:
        st.info(
            "No internal account exists yet.", icon=":material/person_off:"
        )
    else:
        st.dataframe(
            [
                {
                    "username": account.username,
                    "display name": account.display_name,
                    "roles": ", ".join(account.roles),
                    "active": account.is_active,
                }
                for account in accounts
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.markdown("#### Create an internal account")
    st.caption(
        "A role is always assigned here by an administrator; a user can never "
        "choose their own role."
    )
    with st.form("create_user"):
        username = st.text_input("Username")
        display_name = st.text_input("Display name")
        email = st.text_input("Email", value="")
        role_choice = st.selectbox(
            "Role",
            options=list(Role),
            format_func=role_label,
        )
        secret = st.text_input("Initial password", type="password")
        submitted = st.form_submit_button(
            "Create account", icon=":material/person_add:", type="primary"
        )
    if not submitted:
        return
    if not username.strip() or not secret:
        st.error(
            "A username and an initial password are required.",
            icon=":material/error:",
        )
        return
    provider = LocalPasswordAuthenticationProvider()
    try:
        provider.create_user(
            username=username,
            password=secret,
            roles=(role_choice,),
            display_name=display_name,
            email=email,
        )
    except (AuthenticationError, UnknownRoleError) as error:
        st.error(str(error), icon=":material/error:")
        return
    st.success(
        f"Account {username.strip().casefold()} was created.",
        icon=":material/task_alt:",
    )
    st.rerun()


def render_system(user) -> None:
    st.title(":material/settings: System configuration")
    if not user.has_permission(Permission.CONFIGURE_SYSTEM):
        st.error(
            "Your role does not include system configuration.",
            icon=":material/block:",
        )
        return

    st.caption(
        "Read-only view of the effective configuration. No secret value is "
        "displayed; a secret-backed setting reports presence only."
    )
    try:
        report = runtime_report()
    except Exception:  # noqa: BLE001 - status must never break the page
        st.warning(
            f"Application version {APP_VERSION}. The startup status is "
            "currently unavailable.",
            icon=":material/info:",
        )
        return

    tiles = st.columns(3, gap="medium", border=True)
    tiles[0].metric("Version", report.version)
    tiles[1].metric("Mode", report.application_mode)
    tiles[2].metric("Pricing data", report.pricing_data_mode)

    st.markdown("#### Storage")
    st.caption(f"Database mode: {report.database_mode}")
    st.caption(f"Target: {report.database_target}")
    st.caption(f"Persistence: {report.database_persistence}")

    st.markdown("#### Application switches")
    st.caption(f"Demo mode: {'on' if DEMO_MODE else 'off'}")
    st.caption(f"Pricing data mode: {PRICING_DATA_MODE}")
    st.caption(
        "Internal cost visibility: "
        + ("enabled" if SHOW_INTERNAL_COSTS else "disabled")
    )

    st.markdown("#### Agent providers")
    st.dataframe(
        [
            {
                "agent": agent["label"],
                "provider": agent["provider"],
                "mode": agent["mode"],
                "API key": (
                    "present" if agent["api_key_present"] else "not configured"
                ),
                "fallback": "deterministic",
            }
            for agent in report.agents
        ],
        use_container_width=True,
        hide_index=True,
    )
