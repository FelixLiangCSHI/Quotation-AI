"""Column mapping and normalisation tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.ingestion.config import IngestionConfig
from app.ingestion.mapping import (
    ColumnMappingProfile,
    MappingError,
    build_profile,
    normalize_header,
    suggest_mapping,
)
from app.ingestion.normalization import NormalizationError, normalize_value
from app.ingestion.schemas import DatasetKind, FieldKind, get_schema
from tests.fixtures import excel_fixtures


def test_headers_are_matched_through_configurable_sap_aliases():
    mapping = suggest_mapping(
        ["Material", "Material Description", "MEINS", "Region"],
        DatasetKind.PRODUCT_MASTER,
    )

    assert mapping["product_id"] == "Material"
    assert mapping["description"] == "Material Description"
    assert mapping["unit_of_measure"] == "MEINS"
    assert mapping["region"] == "Region"


def test_deployment_specific_aliases_can_be_supplied_without_code_changes():
    mapping = suggest_mapping(
        ["Artikelnummer", "Bezeichnung"],
        DatasetKind.PRODUCT_MASTER,
        extra_aliases={
            "Artikelnummer": "product_id",
            "Bezeichnung": "description",
        },
    )

    assert mapping == {
        "product_id": "Artikelnummer",
        "description": "Bezeichnung",
    }


def test_header_normalization_ignores_case_punctuation_and_spacing():
    assert normalize_header("Cat#") == normalize_header("cat")
    assert normalize_header(" Net  Price ") == "net price"
    assert normalize_header("Service Margin %") == "service margin percent"


def test_user_overrides_win_over_suggestions():
    profile = build_profile(
        name="pricing",
        dataset_kind=DatasetKind.PRICING,
        sheet_name="Pricing",
        headers=list(excel_fixtures.PRICING_HEADERS),
        overrides={"description": "Cat#", "product_id": "Description"},
    )

    assert profile.field_to_header["description"] == "Cat#"
    assert profile.field_to_header["product_id"] == "Description"


def test_a_mapping_missing_required_fields_is_rejected():
    with pytest.raises(MappingError) as error:
        build_profile(
            name="pricing",
            dataset_kind=DatasetKind.PRICING,
            sheet_name="Pricing",
            headers=["Cat#", "Description"],
        )

    assert "net_price" in str(error.value)


def test_mapping_the_same_column_twice_is_rejected():
    profile = ColumnMappingProfile(
        name="pricing",
        dataset_kind=DatasetKind.PRICING,
        sheet_name="Pricing",
        field_to_header={
            "product_id": "Cat#",
            "description": "Cat#",
            "list_price": "List Price",
            "net_price": "Net Price",
            "currency": "Currency",
        },
    )

    with pytest.raises(MappingError) as error:
        profile.validate()

    assert "more than one canonical field" in str(error.value)


def test_an_unknown_canonical_field_is_rejected():
    profile = ColumnMappingProfile(
        name="pricing",
        dataset_kind=DatasetKind.PRICING,
        sheet_name="Pricing",
        field_to_header={"not_a_field": "Cat#"},
    )

    with pytest.raises(MappingError):
        profile.validate()


def test_a_mapping_profile_round_trips_through_a_document():
    profile = build_profile(
        name="reusable",
        dataset_kind=DatasetKind.PRICING,
        sheet_name="Pricing",
        headers=list(excel_fixtures.PRICING_HEADERS),
    )

    restored = ColumnMappingProfile.from_dict(profile.to_dict())

    assert restored == profile


def test_every_canonical_schema_declares_required_fields():
    for kind in DatasetKind:
        schema = get_schema(kind)
        assert schema.required_fields, kind


@pytest.mark.parametrize(
    ("raw", "kind", "expected"),
    [
        ("  Synthetic  system ", FieldKind.TEXT, "Synthetic system"),
        (100.0, FieldKind.IDENTIFIER, "100"),
        ("100.00", FieldKind.IDENTIFIER, "100"),
        ("SYN-100 ", FieldKind.IDENTIFIER, "SYN-100"),
        ("1,234.50", FieldKind.DECIMAL, Decimal("1234.50")),
        ("(500)", FieldKind.DECIMAL, Decimal("-500")),
        ("$2,000", FieldKind.DECIMAL, Decimal("2000")),
        ("12%", FieldKind.DECIMAL, Decimal("12")),
        (3, FieldKind.INTEGER, 3),
        ("2026-03-01", FieldKind.DATE, date(2026, 3, 1)),
        ("01/03/2026", FieldKind.DATE, date(2026, 3, 1)),
        ("usd", FieldKind.CURRENCY, "USD"),
        ("us", FieldKind.REGION, "US"),
        ("each", FieldKind.UNIT, "EA"),
    ],
)
def test_values_normalise_to_canonical_types(raw, kind, expected):
    assert normalize_value(raw, kind).value == expected


@pytest.mark.parametrize(
    "raw",
    ["", "  ", "n/a", "N/A", "-", None, "null"],
)
def test_blank_tokens_normalise_to_none(raw):
    assert normalize_value(raw, FieldKind.DECIMAL).value is None


@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("not-a-number", FieldKind.DECIMAL),
        ("1.5", FieldKind.INTEGER),
        ("31/02/2026", FieldKind.DATE),
        ("nonsense", FieldKind.DATE),
    ],
)
def test_unparseable_values_raise(raw, kind):
    with pytest.raises(NormalizationError):
        normalize_value(raw, kind)


def test_unit_aliasing_emits_a_warning():
    normalized = normalize_value("each", FieldKind.UNIT, config=IngestionConfig())

    assert normalized.value == "EA"
    assert normalized.warnings
