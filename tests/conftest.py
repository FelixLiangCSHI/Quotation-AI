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
