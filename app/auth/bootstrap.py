"""Initial internal account bootstrap.

A locally managed deployment needs one administrator before anybody can sign
in and manage further users. Credentials are never hard-coded: the initial
administrator password is taken from the environment, and when it is absent a
random one is generated and returned once so an operator can record it.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from app.auth.local_provider import LocalPasswordAuthenticationProvider
from app.auth.provider import AuthenticationError
from app.auth.roles import Role

ADMIN_USERNAME_ENV = "QUOTATION_ADMIN_USERNAME"
ADMIN_PASSWORD_ENV = "QUOTATION_ADMIN_PASSWORD"
DEFAULT_ADMIN_USERNAME = "administrator"


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of the bootstrap attempt."""

    username: str
    created: bool
    #: Only populated when this call generated the password itself, so it can
    #: be shown once to the operator. Never persisted in clear text.
    generated_password: str | None = None


def ensure_initial_administrator(
    provider: LocalPasswordAuthenticationProvider | None = None,
) -> BootstrapResult:
    """Create the first administrator account when no user exists yet."""

    auth = provider or LocalPasswordAuthenticationProvider()
    if auth.has_any_user():
        return BootstrapResult(username="", created=False)

    username = os.getenv(ADMIN_USERNAME_ENV, DEFAULT_ADMIN_USERNAME).strip()
    configured = os.getenv(ADMIN_PASSWORD_ENV, "")
    password = configured or secrets.token_urlsafe(18)
    try:
        auth.create_user(
            username=username,
            password=password,
            roles=(Role.ADMINISTRATOR,),
            display_name="Initial administrator",
        )
    except AuthenticationError:
        return BootstrapResult(username=username, created=False)
    return BootstrapResult(
        username=username,
        created=True,
        generated_password=None if configured else password,
    )
