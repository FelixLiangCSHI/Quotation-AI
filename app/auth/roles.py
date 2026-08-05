"""Central role and permission definitions (Phase 6).

Roles are a closed enumeration. A user can never self-declare a role with free
text: every role assignment must resolve to a member of :class:`Role`, and the
permission map below is the single authority for what a role may do.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable


class Role(str, Enum):
    """The internal roles recognised by the approval workflow."""

    SALES_USER = "sales_user"
    SALES_MANAGER = "sales_manager"
    PRICING_MANAGER = "pricing_manager"
    ADMINISTRATOR = "administrator"


class Permission(str, Enum):
    """Every capability the service layer checks before acting."""

    CREATE_QUOTATION = "create_quotation"
    EDIT_OWN_DRAFT = "edit_own_draft"
    RUN_PRICING = "run_pricing"
    RUN_VALIDATION = "run_validation"
    SUBMIT_QUOTATION = "submit_quotation"
    VIEW_OWN_QUOTATIONS = "view_own_quotations"
    RESPOND_TO_REVISION = "respond_to_revision"

    VIEW_APPROVAL_TASKS = "view_approval_tasks"
    APPROVE_PASS = "approve_pass"
    APPROVE_WITH_OVERRIDE = "approve_with_override"
    REQUEST_REVISION = "request_revision"
    REJECT_QUOTATION = "reject_quotation"

    VIEW_COMMERCIAL_DETAIL = "view_commercial_detail"
    MANAGE_POLICY_VERSIONS = "manage_policy_versions"

    MANAGE_USERS = "manage_users"
    MANAGE_DATA_VERSIONS = "manage_data_versions"
    VIEW_AUDIT_RECORDS = "view_audit_records"
    CONFIGURE_SYSTEM = "configure_system"


_SALES_USER_PERMISSIONS = frozenset(
    {
        Permission.CREATE_QUOTATION,
        Permission.EDIT_OWN_DRAFT,
        Permission.RUN_PRICING,
        Permission.RUN_VALIDATION,
        Permission.SUBMIT_QUOTATION,
        Permission.VIEW_OWN_QUOTATIONS,
        Permission.RESPOND_TO_REVISION,
    }
)

_APPROVER_PERMISSIONS = frozenset(
    {
        Permission.VIEW_APPROVAL_TASKS,
        Permission.APPROVE_PASS,
        Permission.APPROVE_WITH_OVERRIDE,
        Permission.REQUEST_REVISION,
        Permission.REJECT_QUOTATION,
        Permission.VIEW_OWN_QUOTATIONS,
    }
)

#: The single, central role-to-permission map.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.SALES_USER: _SALES_USER_PERMISSIONS,
    Role.SALES_MANAGER: _APPROVER_PERMISSIONS,
    Role.PRICING_MANAGER: _APPROVER_PERMISSIONS
    | frozenset(
        {
            Permission.VIEW_COMMERCIAL_DETAIL,
            Permission.MANAGE_POLICY_VERSIONS,
        }
    ),
    Role.ADMINISTRATOR: frozenset(
        {
            Permission.MANAGE_USERS,
            Permission.MANAGE_DATA_VERSIONS,
            Permission.MANAGE_POLICY_VERSIONS,
            Permission.VIEW_AUDIT_RECORDS,
            Permission.CONFIGURE_SYSTEM,
            Permission.VIEW_COMMERCIAL_DETAIL,
            Permission.VIEW_APPROVAL_TASKS,
        }
    ),
}

#: Roles that may be assigned as the responsible approver on a task.
APPROVER_ROLES: tuple[Role, ...] = (Role.SALES_MANAGER, Role.PRICING_MANAGER)


class UnknownRoleError(ValueError):
    """Raised when a role value is not a member of :class:`Role`."""


def parse_role(value: object) -> Role:
    """Resolve ``value`` to a known :class:`Role`.

    Free-text role names are refused; this is the only supported way to turn
    stored or submitted text into a role.
    """

    if isinstance(value, Role):
        return value
    if isinstance(value, str):
        candidate = value.strip().casefold().replace(" ", "_").replace("-", "_")
        for role in Role:
            if candidate == role.value:
                return role
    raise UnknownRoleError(f"Unknown role: {value!r}")


def parse_roles(values: Iterable[object]) -> tuple[Role, ...]:
    seen: list[Role] = []
    for value in values:
        role = parse_role(value)
        if role not in seen:
            seen.append(role)
    return tuple(seen)


def permissions_for(roles: Iterable[Role]) -> frozenset[Permission]:
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(parse_role(role), frozenset())
    return frozenset(granted)


def role_label(role: Role) -> str:
    return parse_role(role).value.replace("_", " ").title()
