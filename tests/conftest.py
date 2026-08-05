"""Shared test fixtures.

Every test runs against a throwaway SQLite database created from the ORM
metadata, so no external database is required.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import event

from app.db.base import Base
from app.db.session import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from app.domain.dto import LineItemDTO, LineItemType
from app.services.quotation_service import QuotationService


@pytest.fixture()
def engine():
    engine = create_database_engine(
        DatabaseSettings(url="sqlite+pysqlite:///:memory:")
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def session_factory(engine):
    return create_session_factory(engine)


@pytest.fixture()
def service(session_factory) -> QuotationService:
    return QuotationService(session_factory)


@pytest.fixture()
def three_line_items() -> tuple[LineItemDTO, ...]:
    """A main product, an accessory and a service on one quotation."""

    return (
        LineItemDTO(
            position=0,
            item_type=LineItemType.MAIN_PRODUCT,
            product_id="SYN-MAIN-1",
            customer_description="Synthetic imaging system",
            internal_description="cost basis: synthetic fixture",
            quantity=1,
            proposed_unit_price=Decimal("100000.00"),
        ),
        LineItemDTO(
            position=1,
            item_type=LineItemType.ACCESSORY,
            product_id="SYN-ACC-1",
            customer_description="Synthetic detector grid",
            quantity=2,
            proposed_unit_price=Decimal("2500.50"),
        ),
        LineItemDTO(
            position=2,
            item_type=LineItemType.SERVICE,
            product_id="SYN-SVC-1",
            customer_description="Installation and commissioning",
            quantity=1,
            proposed_unit_price=Decimal("7500.25"),
        ),
    )


@pytest.fixture()
def auth_provider(session_factory):
    from app.auth import LocalPasswordAuthenticationProvider

    return LocalPasswordAuthenticationProvider(session_factory)


@pytest.fixture()
def approval_service(session_factory, service):
    from app.services.approval_service import ApprovalService

    return ApprovalService(session_factory, service)


@pytest.fixture()
def audit_service(session_factory):
    from app.services.audit_view import AuditViewService

    return AuditViewService(session_factory)


@pytest.fixture()
def people(auth_provider):
    """One authenticated principal per role."""

    from app.auth import Role
    from tests.fixtures.phase6_helpers import create_user

    return {
        "sales": create_user(auth_provider, "sam.sales", Role.SALES_USER),
        "manager": create_user(
            auth_provider, "mia.manager", Role.SALES_MANAGER
        ),
        "pricing": create_user(
            auth_provider, "pat.pricing", Role.PRICING_MANAGER
        ),
        "admin": create_user(auth_provider, "avi.admin", Role.ADMINISTRATOR),
    }


@pytest.fixture()
def email_provider():
    from app.emailing.providers import ConsoleEmailProvider

    return ConsoleEmailProvider()


@pytest.fixture()
def email_config():
    from tests.fixtures.phase7_helpers import email_config as build

    return build()


@pytest.fixture()
def email_service(session_factory, email_config, email_provider):
    from app.emailing.service import EmailService

    return EmailService(
        session_factory, config=email_config, provider=email_provider
    )


@pytest.fixture()
def reminder_worker(session_factory, email_service, email_config):
    from app.emailing.reminders import ApprovalReminderWorker

    return ApprovalReminderWorker(
        session_factory, email_service=email_service, config=email_config
    )


@pytest.fixture()
def document_service(session_factory, service):
    from app.services.document_service import DocumentService

    return DocumentService(session_factory, quotation_service=service)
