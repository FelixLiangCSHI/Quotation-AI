"""Seed one account per role so a demo can be run immediately.

These accounts exist for demo and pilot environments with synthetic data only.
They share the well-known demo password :data:`DEFAULT_DEMO_PASSWORD` so a
reviewer can sign in without any setup step. A deployment that needs a
different password sets ``QUOTATION_DEMO_PASSWORD``; the password is always
hashed before it reaches the database and is never printed by the command line.

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

#: Shared password for every demo account. ``QUOTATION_DEMO_PASSWORD``
#: overrides it. Demo accounts hold synthetic data only, which is why a
#: deliberately simple shared password is acceptable here and nowhere else.
DEMO_PASSWORD_ENV = "QUOTATION_DEMO_PASSWORD"
DEFAULT_DEMO_PASSWORD = "123456"


def demo_password() -> str:
    """Return the shared demo password, environment override first."""

    return os.getenv(DEMO_PASSWORD_ENV, "").strip() or DEFAULT_DEMO_PASSWORD


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
    #: Only populated for an account this call created, so a caller can hand
    #: it over out of band. Never printed by the command line.
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
    shared = password if password is not None else demo_password()
    results: list[SeededAccount] = []
    for username, role, display_name in DEMO_ACCOUNTS:
        secret = shared or secrets.token_urlsafe(12)
        try:
            auth.create_user(
                username=username,
                password=secret,
                roles=(role,),
                display_name=display_name,
                allow_weak_password=True,
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
            "Create one demo account per role. The shared password defaults "
            "to the documented demo password and can be overridden with "
            "QUOTATION_DEMO_PASSWORD. It is never printed or stored in clear "
            "text."
        ),
    )
    parser.parse_args(argv)

    _ensure_schema()
    accounts = seed_demo_accounts()
    print("Demo accounts:")
    for account in accounts:
        state = "created" if account.created else "already exists (unchanged)"
        print(f"  {account.username:22} {account.role.value:16} {state}")
    print(
        "\nSign in with the username above and the shared demo password "
        "(QUOTATION_DEMO_PASSWORD when set, otherwise the documented "
        "default)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
