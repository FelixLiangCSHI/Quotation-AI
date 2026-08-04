"""Publication, activation and idempotency tests for pricing data versions."""

from __future__ import annotations

import pytest

from app.ingestion.mapping import build_profile
from app.ingestion.repository import (
    SYNTHETIC_VERSION_LABEL,
    DuplicateImportError,
    PricingDataRepository,
    PricingDataRepositoryError,
    PricingDataVersionNotFoundError,
)
from app.ingestion.schemas import DatasetKind
from app.ingestion.storage import InMemoryWorkbookStorage
from tests.fixtures import excel_fixtures
from tests.fixtures.ingestion_helpers import import_fixture


@pytest.fixture()
def repository(session_factory) -> PricingDataRepository:
    return PricingDataRepository(session_factory)


@pytest.fixture()
def clean_preview():
    return import_fixture("valid.xlsx", excel_fixtures.valid_workbook())


def test_a_staged_version_records_the_full_provenance(repository, clean_preview):
    summary = repository.create_version_from_preview(
        clean_preview,
        label="2026-Q1",
        uploaded_by="internal.user",
        notes="First synthetic import",
    )

    assert summary.status == "staged"
    assert summary.is_active is False
    assert summary.source_filename == "valid.xlsx"
    assert summary.checksum == clean_preview.workbook.content_hash
    assert summary.uploaded_by == "internal.user"
    assert summary.uploaded_at is not None
    assert summary.row_count == 14
    assert summary.rejected_row_count == 0
    assert set(summary.mapping_profile) == {kind.value for kind in DatasetKind}
    assert summary.validation_summary["counts"]["valid"] == 14
    assert summary.notes == "First synthetic import"


def test_the_raw_workbook_is_kept_outside_the_repository(repository):
    storage = InMemoryWorkbookStorage()
    preview = import_fixture(
        "valid.xlsx", excel_fixtures.valid_workbook(), storage=storage
    )

    summary = repository.create_version_from_preview(
        preview, label="stored", uploaded_by="internal.user"
    )

    assert summary.storage_uri.startswith("memory://")
    assert storage.exists(summary.storage_uri)


def test_local_storage_writes_under_the_configured_root(tmp_path):
    from app.ingestion.storage import LocalWorkbookStorage

    storage = LocalWorkbookStorage(tmp_path / "secure")
    payload = excel_fixtures.valid_workbook()

    uri = storage.store(
        content_hash="a" * 64, filename="valid.xlsx", payload=payload
    )

    assert storage.retrieve(uri) == payload
    assert (tmp_path / "secure").exists()


def test_a_staged_version_is_not_readable_by_the_pricing_engine(
    repository, clean_preview
):
    summary = repository.create_version_from_preview(
        clean_preview, label="staged-only", uploaded_by="internal.user"
    )

    with pytest.raises(PricingDataRepositoryError):
        repository.records_for_version(summary.id)


def test_publication_and_activation_are_separate_explicit_actions(
    repository, clean_preview
):
    staged = repository.create_version_from_preview(
        clean_preview, label="2026-Q1", uploaded_by="internal.user"
    )

    published = repository.publish(staged.id)
    assert published.status == "published"
    assert published.published_at is not None
    assert published.is_active is False
    assert repository.get_active_version() is None

    activated = repository.activate(staged.id)
    assert activated.is_active is True
    assert activated.activated_at is not None
    assert repository.get_active_version().id == staged.id


def test_an_unpublished_version_cannot_be_activated(repository, clean_preview):
    staged = repository.create_version_from_preview(
        clean_preview, label="2026-Q1", uploaded_by="internal.user"
    )

    with pytest.raises(PricingDataRepositoryError):
        repository.activate(staged.id)


def test_the_pricing_engine_reads_the_active_published_version(
    repository, clean_preview
):
    version = repository.create_version_from_preview(
        clean_preview, label="2026-Q1", uploaded_by="internal.user"
    )
    repository.publish(version.id)
    repository.activate(version.id)

    pricing_records = repository.records_for_active_version(DatasetKind.PRICING)

    assert len(pricing_records) == 3
    assert {record.product_id for record in pricing_records} == {
        "SYN-100",
        "SYN-200",
        "SYN-300",
    }
    assert pricing_records[0].values["net_price"] == "100000"
    assert pricing_records[0].values["currency"] == "USD"


def test_rejected_rows_never_enter_the_published_dataset(repository):
    preview = import_fixture(
        "currencies.xlsx", excel_fixtures.mixed_currencies_workbook()
    )
    version = repository.create_version_from_preview(
        preview, label="with-rejections", uploaded_by="internal.user"
    )
    repository.publish(version.id)
    repository.activate(version.id)

    pricing_records = repository.records_for_active_version(DatasetKind.PRICING)
    rejections = repository.rejections_for_version(version.id)

    assert [record.product_id for record in pricing_records] == ["SYN-100"]
    assert len(rejections) == 2
    assert version.rejected_row_count == 2
    assert all(rejection["issues"] for rejection in rejections)


def test_a_previous_version_can_be_reactivated(repository, clean_preview):
    first = repository.create_version_from_preview(
        clean_preview, label="v1", uploaded_by="internal.user"
    )
    repository.publish(first.id)
    repository.activate(first.id)

    second_preview = import_fixture(
        "unknown.xlsx", excel_fixtures.unknown_products_workbook()
    )
    second = repository.create_version_from_preview(
        second_preview, label="v2", uploaded_by="internal.user"
    )
    repository.publish(second.id)
    repository.activate(second.id)

    assert repository.get_active_version().label == "v2"

    repository.activate(first.id)

    active = repository.get_active_version()
    assert active.label == "v1"
    assert [
        version.is_active
        for version in repository.list_versions()
        if version.label == "v2"
    ] == [False]


def test_importing_the_same_workbook_twice_is_rejected_by_file_hash(
    repository, clean_preview
):
    repository.create_version_from_preview(
        clean_preview, label="v1", uploaded_by="internal.user"
    )
    repeat = import_fixture("valid-copy.xlsx", excel_fixtures.valid_workbook())

    assert repeat.workbook.content_hash == clean_preview.workbook.content_hash

    with pytest.raises(DuplicateImportError) as error:
        repository.create_version_from_preview(
            repeat, label="v2", uploaded_by="internal.user"
        )

    assert "already imported" in str(error.value)
    assert len(repository.list_versions()) == 1
    assert (
        repository.find_version_by_checksum(
            clean_preview.workbook.content_hash
        ).label
        == "v1"
    )


def test_a_duplicate_import_can_be_forced_when_the_user_confirms(
    repository, clean_preview
):
    repository.create_version_from_preview(
        clean_preview, label="v1", uploaded_by="internal.user"
    )

    forced = repository.create_version_from_preview(
        clean_preview,
        label="v1-reimport",
        uploaded_by="internal.user",
        allow_duplicate_hash=True,
    )

    assert forced.label == "v1-reimport"
    assert len(repository.list_versions()) == 2


def test_duplicate_labels_are_rejected(repository, clean_preview):
    repository.create_version_from_preview(
        clean_preview, label="v1", uploaded_by="internal.user"
    )
    other = import_fixture(
        "unknown.xlsx", excel_fixtures.unknown_products_workbook()
    )

    with pytest.raises(PricingDataRepositoryError):
        repository.create_version_from_preview(
            other, label="v1", uploaded_by="internal.user"
        )


def test_an_unknown_version_cannot_be_published(repository):
    with pytest.raises(PricingDataVersionNotFoundError):
        repository.publish(4242)


def test_the_synthetic_dataset_is_preserved_as_a_fallback(repository):
    fallback = repository.register_synthetic_fallback(row_count=12)

    assert fallback.label == SYNTHETIC_VERSION_LABEL
    assert fallback.status == "published"
    assert fallback.is_active is False
    assert "synthetic" in fallback.notes.casefold()

    # Registering twice is idempotent.
    assert repository.register_synthetic_fallback().id == fallback.id

    repository.activate(fallback.id)
    assert repository.get_active_version().label == SYNTHETIC_VERSION_LABEL


def test_mapping_profiles_are_saved_and_reused(repository):
    profile = build_profile(
        name="sap-pricing-standard",
        dataset_kind=DatasetKind.PRICING,
        sheet_name="Pricing",
        headers=list(excel_fixtures.PRICING_HEADERS),
    )

    repository.save_mapping_profile(profile, created_by="internal.user")
    restored = repository.load_mapping_profile("sap-pricing-standard")

    assert restored == profile
    assert [item.name for item in repository.list_mapping_profiles()] == [
        "sap-pricing-standard"
    ]

    # Saving again updates in place rather than duplicating.
    repository.save_mapping_profile(profile, created_by="internal.user")
    assert len(repository.list_mapping_profiles()) == 1
