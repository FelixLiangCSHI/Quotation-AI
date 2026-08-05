"""Role-based workspace navigation.

The sidebar is derived from the signed-in user's permissions, not from a
hard-coded page list, so a role can never see an entry the service layer would
refuse. This module is pure data and holds no Streamlit dependency, which
keeps it testable on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.auth.provider import AuthenticatedUser
from app.auth.roles import Permission, Role

__all__ = [
    "WORKSPACES",
    "WorkspaceEntry",
    "default_page_key",
    "landing_headline",
    "pages_for",
    "workspace_label",
]


@dataclass(frozen=True)
class WorkspaceEntry:
    """One sidebar entry."""

    key: str
    label: str
    icon: str
    section: str
    #: The page is listed only when the user holds one of these permissions.
    #: An empty tuple means every authenticated user may open it.
    permissions: tuple[Permission, ...] = ()

    def is_visible_to(self, user: AuthenticatedUser) -> bool:
        if not self.permissions:
            return True
        return any(user.has_permission(item) for item in self.permissions)


#: Every page the shell can show, in sidebar order. ``dashboard`` is always
#: first so each role lands on its own starting page.
WORKSPACES: tuple[WorkspaceEntry, ...] = (
    WorkspaceEntry(
        key="dashboard",
        label="Dashboard",
        icon=":material/dashboard:",
        section="Workspace",
    ),
    WorkspaceEntry(
        key="create_quotation",
        label="Create Quotation",
        icon=":material/add_circle:",
        section="Sales",
        permissions=(Permission.CREATE_QUOTATION,),
    ),
    WorkspaceEntry(
        key="my_quotations",
        label="My Quotations",
        icon=":material/folder_open:",
        section="Sales",
        permissions=(Permission.VIEW_OWN_QUOTATIONS,),
    ),
    WorkspaceEntry(
        key="approval_center",
        label="Approval Center",
        icon=":material/approval:",
        section="Approval",
        permissions=(Permission.VIEW_APPROVAL_TASKS,),
    ),
    WorkspaceEntry(
        key="approval_history",
        label="Approval History",
        icon=":material/fact_check:",
        section="Approval",
        permissions=(Permission.VIEW_APPROVAL_TASKS,),
    ),
    WorkspaceEntry(
        key="pricing_data",
        label="Pricing Data",
        icon=":material/table_chart:",
        section="Administration",
        permissions=(Permission.MANAGE_DATA_VERSIONS,),
    ),
    WorkspaceEntry(
        key="policy",
        label="Policy Management",
        icon=":material/rule:",
        section="Administration",
        permissions=(Permission.MANAGE_POLICY_VERSIONS,),
    ),
    WorkspaceEntry(
        key="users",
        label="User Management",
        icon=":material/group:",
        section="Administration",
        permissions=(Permission.MANAGE_USERS,),
    ),
    WorkspaceEntry(
        key="system",
        label="System Configuration",
        icon=":material/settings:",
        section="Administration",
        permissions=(Permission.CONFIGURE_SYSTEM,),
    ),
    WorkspaceEntry(
        key="documents",
        label="Documents",
        icon=":material/description:",
        section="Records",
        permissions=(
            Permission.VIEW_OWN_QUOTATIONS,
            Permission.VIEW_APPROVAL_TASKS,
        ),
    ),
    WorkspaceEntry(
        key="email",
        label="Email Centre",
        icon=":material/outgoing_mail:",
        section="Records",
        permissions=(
            Permission.VIEW_OWN_QUOTATIONS,
            Permission.VIEW_APPROVAL_TASKS,
        ),
    ),
    WorkspaceEntry(
        key="audit",
        label="Audit",
        icon=":material/history:",
        section="Records",
        permissions=(Permission.VIEW_AUDIT_RECORDS,),
    ),
)

_BY_KEY = {entry.key: entry for entry in WORKSPACES}

#: The page each role starts on after signing in.
_ROLE_LANDING: dict[Role, str] = {
    Role.SALES_USER: "dashboard",
    Role.SALES_MANAGER: "dashboard",
    Role.PRICING_MANAGER: "dashboard",
    Role.ADMINISTRATOR: "dashboard",
}

#: The headline shown on each role's dashboard, so two roles never see the
#: same starting page.
_ROLE_HEADLINE: dict[Role, str] = {
    Role.SALES_USER: "Quotation creation dashboard",
    Role.SALES_MANAGER: "Approval center",
    Role.PRICING_MANAGER: "Pricing and policy control",
    Role.ADMINISTRATOR: "Administration console",
}


def pages_for(user: AuthenticatedUser) -> tuple[WorkspaceEntry, ...]:
    """Return the sidebar entries this user may open."""

    return tuple(entry for entry in WORKSPACES if entry.is_visible_to(user))


def default_page_key(user: AuthenticatedUser) -> str:
    """Return the page a user lands on immediately after signing in."""

    visible = {entry.key for entry in pages_for(user)}
    for role in user.roles:
        candidate = _ROLE_LANDING.get(role)
        if candidate and candidate in visible:
            return candidate
    return next(iter(entry.key for entry in pages_for(user)), "dashboard")


def landing_headline(user: AuthenticatedUser) -> str:
    """Return the role-specific dashboard headline."""

    return _ROLE_HEADLINE.get(user.primary_role, "Workspace")


def workspace_label(key: str) -> str:
    entry = _BY_KEY.get(key)
    return entry.label if entry else key.replace("_", " ").title()


def visible_keys(entries: Iterable[WorkspaceEntry]) -> tuple[str, ...]:
    return tuple(entry.key for entry in entries)
