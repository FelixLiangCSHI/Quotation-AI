"""Authentication, roles and permissions (Phase 6)."""

from __future__ import annotations

from app.auth.local_provider import (
    AccountLockedError,
    LocalPasswordAuthenticationProvider,
)
from app.auth.passwords import (
    WeakPasswordError,
    hash_password,
    verify_password,
)
from app.auth.provider import (
    DEFAULT_SESSION_LIFETIME,
    AuthenticatedUser,
    AuthenticationError,
    AuthenticationProvider,
    EnterpriseSsoAuthenticationProvider,
    PermissionDeniedError,
)
from app.auth.roles import (
    APPROVER_ROLES,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    UnknownRoleError,
    parse_role,
    parse_roles,
    permissions_for,
    role_label,
)

__all__ = [
    "APPROVER_ROLES",
    "DEFAULT_SESSION_LIFETIME",
    "ROLE_PERMISSIONS",
    "AuthenticatedUser",
    "AuthenticationError",
    "AuthenticationProvider",
    "EnterpriseSsoAuthenticationProvider",
    "AccountLockedError",
    "LocalPasswordAuthenticationProvider",
    "Permission",
    "PermissionDeniedError",
    "Role",
    "UnknownRoleError",
    "WeakPasswordError",
    "hash_password",
    "parse_role",
    "parse_roles",
    "permissions_for",
    "role_label",
    "verify_password",
]
