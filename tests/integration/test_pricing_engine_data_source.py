"""The pricing engine must price against an explicitly selected version."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.ingestion.pricing_source import resolve_pricing_source
from app.ingestion.repository import PricingDataRepository
from app.pricing_engine import PricingEngine
from tests.fixtures import excel_fixtures
from tests.fixtures.ingestion_helpers import import_fixture


@pytest.fixture()
def repository(session_factory) -> PricingDataRepository:
    return PricingDataRepository(session_factory)


def _publish(repository: PricingDataRepository, payload: bytes, label: str):
    preview = import_fixture(f"{label}.xlsx", payload)
    version = repository.create_version_from_preview(
        preview, label=label, uploaded_by="internal.user"
    )
    return repository.publish(version.id)


def test_without_an_active_version_the_synthetic_dataset_is_the_fallback(
    repository,
):
    source = resolve_pricing_source(repository)

    assert source.is_synthetic_fallback
    assert source.version_id is None
    assert source.records


def test_the_engine_prices_against_the_active_published_version(repository):
    published = _publish(
        repository, excel_fixtures.valid_workbook(), "active-version"
    )
    repository.activate(published.id)

    source = resolve_pricing_source(repository)
    engine = PricingEngine(records=source.records, catalog_list_prices={})

    assert not source.is_synthetic_fallback
    assert source.version_label == "active-version"
    assert {record.product_id for record in source.records} == {
        "SYN-100",
        "SYN-200",
        "SYN-300",
    }
    assert {record.currency for record in source.records} == {"USD", "EUR"}
    assert engine.records == source.records

    syn_100 = next(
        record for record in source.records if record.product_id == "SYN-100"
    )
    assert syn_100.list_price == Decimal("120000")
    assert syn_100.net_price == Decimal("100000")
    assert syn_100.cogs == Decimal("60000")


def test_a_specific_published_version_can_be_selected_explicitly(repository):
    first = _publish(repository, excel_fixtures.valid_workbook(), "v1")
    second = _publish(
        repository, excel_fixtures.mixed_currencies_workbook(), "v2"
    )
    repository.activate(second.id)

    active = resolve_pricing_source(repository)
    explicit = resolve_pricing_source(repository, version_id=first.id)

    assert active.version_label == "v2"
    assert explicit.version_label == "v1"
    assert len(explicit.records) == 3
    # v2 quarantined the unsupported currency and malformed region rows.
    assert len(active.records) == 1


def test_reactivating_a_previous_version_changes_what_the_engine_reads(
    repository,
):
    first = _publish(repository, excel_fixtures.valid_workbook(), "v1")
    second = _publish(
        repository, excel_fixtures.mixed_currencies_workbook(), "v2"
    )

    repository.activate(first.id)
    assert len(resolve_pricing_source(repository).records) == 3

    repository.activate(second.id)
    assert len(resolve_pricing_source(repository).records) == 1

    repository.activate(first.id)
    assert resolve_pricing_source(repository).version_label == "v1"
    assert len(resolve_pricing_source(repository).records) == 3


def test_publishing_alone_never_changes_what_the_engine_reads(repository):
    first = _publish(repository, excel_fixtures.valid_workbook(), "v1")
    repository.activate(first.id)

    _publish(repository, excel_fixtures.mixed_currencies_workbook(), "v2")

    assert resolve_pricing_source(repository).version_label == "v1"
