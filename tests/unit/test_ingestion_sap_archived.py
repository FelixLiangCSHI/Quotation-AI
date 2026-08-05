"""Ingestion of the desensitised archived SAP price-list export."""

from __future__ import annotations

from decimal import Decimal

from app.config import SAP_BASE_CURRENCY
from app.ingestion.pipeline import run_import, start_import
from app.ingestion.pricing_source import records_from_version
from app.ingestion.repository import PricingDataRecordDTO
from app.ingestion.sap_archived import archived_plans, archived_profile
from app.ingestion.schemas import DatasetKind
from tests.fixtures.excel_fixtures import archived_workbook, valid_workbook


def _session(payload: bytes):
    return start_import("SAP_archived.xlsx", payload)


def test_archived_layout_maps_the_wide_cost_breakdown():
    session = _session(archived_workbook())

    profile = archived_profile(session, "Compass CSI WW")

    assert profile.dataset_kind is DatasetKind.PRICING
    for name in (
        "product_id",
        "description",
        "list_price",
        "net_price",
        "minimum_price",
        "transfer_price",
        "cogs",
        "installation_cogs",
        "warranty_cogs",
        "cogs_installation_warranty",
        "freight",
        "duty",
        "tariff",
    ):
        assert name in profile.field_to_header, name


def test_missing_currency_column_is_supplied_as_a_constant():
    session = _session(archived_workbook())

    profile = archived_profile(session, "Compass CSI WW")

    assert "currency" not in profile.field_to_header
    assert profile.constant_values["currency"] == SAP_BASE_CURRENCY
    assert profile.missing_required_fields() == ()


def test_archived_import_accepts_every_row_with_the_full_cost_basis():
    session = _session(archived_workbook())
    plans, skipped = archived_plans(session)

    preview = run_import(session, plans)

    assert skipped == ()
    assert preview.counts["rejected"] == 0
    assert preview.counts["total"] == 2
    row = preview.datasets[0].result.accepted_rows[0]
    assert row.values["currency"] == SAP_BASE_CURRENCY
    assert row.values["installation_cogs"] == Decimal("4000")
    assert row.values["tariff"] == Decimal("300")


def test_published_archived_rows_reach_the_pricing_engine_shape():
    session = _session(archived_workbook())
    plans, _ = archived_plans(session)
    preview = run_import(session, plans)
    rows = tuple(
        PricingDataRecordDTO(
            dataset_kind=DatasetKind.PRICING,
            source_sheet=preview.datasets[0].sheet_name,
            source_row_number=row.row_number,
            product_id=str(row.values.get("product_id") or ""),
            has_warnings=row.has_warnings,
            values=dict(row.values),
        )
        for row in preview.datasets[0].result.accepted_rows
    )

    records = records_from_version(rows)

    record = next(item for item in records if item.product_id == "SYN-100")
    assert record.currency == SAP_BASE_CURRENCY
    assert record.cogs == Decimal("60000")
    assert record.installation_cogs == Decimal("4000")
    assert record.warranty_cogs == Decimal("2000")
    assert record.freight == Decimal("1500")
    assert record.duty == Decimal("900")
    assert record.tariff == Decimal("300")


def test_a_workbook_without_the_archived_layout_is_reported_not_guessed():
    session = _session(valid_workbook())

    plans, skipped = archived_plans(session)

    assert {plan.profile.sheet_name for plan in plans} == {"Pricing"}
    assert skipped
