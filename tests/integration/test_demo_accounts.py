"""The demo account seeder creates one usable account per role."""

from __future__ import annotations

from app.auth.demo_accounts import (
    DEFAULT_DEMO_PASSWORD,
    DEMO_ACCOUNTS,
    seed_demo_accounts,
)
from app.auth.roles import Role


def test_seeding_creates_one_account_per_role(auth_provider):
    accounts = seed_demo_accounts(auth_provider, password="demo-secret-123")

    assert {account.role for account in accounts} == {
        Role.ADMINISTRATOR,
        Role.SALES_USER,
        Role.SALES_MANAGER,
        Role.PRICING_MANAGER,
    }
    assert all(account.created for account in accounts)
    for username, role, _ in DEMO_ACCOUNTS:
        principal = auth_provider.authenticate(username, "demo-secret-123")
        assert principal.roles == (role,)


def test_generated_passwords_are_unique_and_usable(auth_provider):
    accounts = seed_demo_accounts(auth_provider, password="")

    secrets_used = {account.password for account in accounts}
    assert len(secrets_used) == len(accounts)
    for account in accounts:
        assert account.password
        auth_provider.authenticate(account.username, account.password)


def test_seeding_twice_does_not_change_existing_accounts(auth_provider):
    seed_demo_accounts(auth_provider, password="demo-secret-123")

    again = seed_demo_accounts(auth_provider, password="demo-secret-123")

    assert all(not account.created for account in again)
    assert all(account.password is None for account in again)
    auth_provider.authenticate("demo.admin", "demo-secret-123")


def test_default_password_is_the_documented_demo_password(
    auth_provider, monkeypatch
):
    monkeypatch.delenv("QUOTATION_DEMO_PASSWORD", raising=False)

    seed_demo_accounts(auth_provider)

    for username, _, _ in DEMO_ACCOUNTS:
        auth_provider.authenticate(username, DEFAULT_DEMO_PASSWORD)


def test_environment_overrides_the_default_password(auth_provider, monkeypatch):
    monkeypatch.setenv("QUOTATION_DEMO_PASSWORD", "another-demo-secret")

    seed_demo_accounts(auth_provider)

    auth_provider.authenticate("demo.sales", "another-demo-secret")
