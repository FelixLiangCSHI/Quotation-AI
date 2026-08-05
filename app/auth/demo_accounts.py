"""Seed one account per role so a demo can be run immediately.

Credentials are never hard-coded. Every password is either taken from the
environment (``QUOTATION_DEMO_PASSWORD``) or randomly generated and returned
once so the presenter can record it. Nothing is stored in clear text: the
provider hashes the password before it reaches the database.

Run it with::

    python -m app.auth.demo_accounts
"""

from __future__ import annotations

import argparse
import os
import secrets
from dataclasses import dataclass

from app.auth.local_provider import LocalPasswordAuthenticationProvider
from app.auth.provider import AuthenticationError
from app.auth.roles import Role

#: Optional shared password for every demo account. When it is absent a
#: separate random password is generated per account.
DEMO_PASSWORD_ENV = "QUOTATION_DEMO_PASSWORD"

#: Username and role of each seeded demo account.
DEMO_ACCOUNTS: tuple[tuple[str, Role, str], ...] = (
    ("demo.admin", Role.ADMINISTRATOR, "Demo administrator"),
    ("demo.sales", Role.SALES_USER, "Demo sales user"),
    ("demo.salesmanager", Role.SALES_MANAGER, "Demo sales manager"),
    ("demo.pricingmanager", Role.PRICING_MANAGER, "Demo pricing manager"),
)


@dataclass(frozen=True)
class SeededAccount:
    """One demo account after seeding."""

    username: str
    role: Role
    created: bool
    #: Only populated for an account this call created, so it can be shown
    #: once to the presenter.
    password: str | None = None


def seed_demo_accounts(
    provider: LocalPasswordAuthenticationProvider | None = None,
    *,
    password: str | None = None,
) -> tuple[SeededAccount, ...]:
    """Create the demo accounts that do not exist yet.

    Re-running is safe: an account that already exists is reported with
    ``created=False`` and its password is left untouched.
    """

    auth = provider or LocalPasswordAuthenticationProvider()
    shared = password if password is not None else os.getenv(DEMO_PASSWORD_ENV, "")
    results: list[SeededAccount] = []
    for username, role, display_name in DEMO_ACCOUNTS:
        secret = shared or secrets.token_urlsafe(12)
        try:
            auth.create_user(
                username=username,
                password=secret,
                roles=(role,),
                display_name=display_name,
            )
        except AuthenticationError:
            results.append(SeededAccount(username=username, role=role, created=False))
            continue
        results.append(
            SeededAccount(
                username=username,
                role=role,
                created=True,
                password=secret,
            )
        )
    return tuple(results)


def _ensure_schema() -> None:
    from app.services.workflow_session import ensure_schema

    ensure_schema()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI entry
    parser = argparse.ArgumentParser(
        prog="python -m app.auth.demo_accounts",
        description=(
            "Create one demo account per role. Passwords come from "
            f"{DEMO_PASSWORD_ENV} or are generated and printed once."
        ),
    )
    parser.parse_args(argv)

    _ensure_schema()
    accounts = seed_demo_accounts()
    print("Demo accounts:")
    for account in accounts:
        if account.created:
            print(
                f"  {account.username:22} {account.role.value:16} "
                f"password: {account.password}"
            )
        else:
            print(
                f"  {account.username:22} {account.role.value:16} "
                "already exists (password unchanged)"
            )
    print(
        "\nRecord these credentials now; passwords are hashed and cannot be "
        "shown again."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
