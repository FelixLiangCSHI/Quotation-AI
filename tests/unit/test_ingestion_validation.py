"""Row validation, quarantine and report tests."""

from __future__ import annotations

import json

import pytest

from app.ingestion.mapping import MappingError
from app.ingestion.pipeline import start_import
from app.ingestion.report import (
    render_validation_report_csv,
    render_validation_report_markdown,
    render_validation_summary_json,
)
from app.ingestion.schemas import DatasetKind
from app.ingestion.validation import IssueCode, Severity
from tests.fixtures import excel_fixtures
from tests.fixtures.ingestion_helpers import (
    build_plans,
    import_fixture,
    issue_codes,
)


def dataset(preview, kind: DatasetKind):
    for staged in preview.datasets:
        if staged.dataset_kind is kind:
            return staged
    raise AssertionError(f"{kind} was not imported")


def test_a_clean_workbook_produces_no_rejections():
    preview = import_fixture("valid.xlsx", excel_fixtures.valid_workbook())

    counts = preview.counts
    assert counts["rejected"] == 0
    assert counts["valid"] == 14
    assert counts["total"] == 14


def test_missing_required_columns_are_reported_at_mapping_time():
    session = start_import(
        "missing.xlsx", excel_fixtures.missing_columns_workbook()
    )

    with pytest.raises(MappingError) as error:
        build_plans(session)

    message = str(error.value)
    assert "net_price" in message and "currency" in message


def test_duplicate_product_ids_and_duplicate_rows_are_rejected():
    preview = import_fixture(
        "duplicates.xlsx", excel_fixtures.duplicate_rows_workbook()
    )

    products = dataset(preview, DatasetKind.PRODUCT_MASTER).result
    pricing = dataset(preview, DatasetKind.PRICING).result

    assert len(products.rejected_rows) == 1
    assert products.rejected_rows[0].issues[0].code is IssueCode.DUPLICATE_PRODUCT_ID
    assert len(pricing.rejected_rows) == 1
    assert pricing.rejected_rows[0].issues[0].code is IssueCode.DUPLICATE_ROW


def test_malformed_prices_are_quarantined():
    preview = import_fixture(
        "prices.xlsx", excel_fixtures.malformed_prices_workbook()
    )

    pricing = dataset(preview, DatasetKind.PRICING).result
    codes = {
        issue.code
        for row in pricing.rejected_rows
        for issue in row.issues
    }

    assert len(pricing.rejected_rows) == 3
    assert IssueCode.INVALID_NUMBER in codes
    assert IssueCode.EMPTY_PRICE in codes
    assert IssueCode.IMPOSSIBLE_PRICE_RELATIONSHIP in codes


def test_cost_above_net_price_is_a_warning_not_a_rejection():
    preview = import_fixture(
        "prices.xlsx", excel_fixtures.malformed_prices_workbook()
    )
    pricing = dataset(preview, DatasetKind.PRICING).result

    all_issues = [
        issue
        for row in pricing.rejected_rows + pricing.warning_rows
        for issue in row.issues
    ]
    cost_issues = [
        issue
        for issue in all_issues
        if issue.code is IssueCode.IMPOSSIBLE_COST_RELATIONSHIP
    ]

    assert cost_issues
    assert all(issue.severity is Severity.WARNING for issue in cost_issues)


def test_unsupported_currency_and_malformed_region_are_rejected():
    preview = import_fixture(
        "currencies.xlsx", excel_fixtures.mixed_currencies_workbook()
    )

    pricing = dataset(preview, DatasetKind.PRICING).result
    codes = issue_codes(preview)

    assert len(pricing.rejected_rows) == 2
    assert IssueCode.UNSUPPORTED_CURRENCY.value in codes
    assert IssueCode.MALFORMED_REGION.value in codes
    # The clean USD row still survives.
    assert len(pricing.valid_rows) == 1


def test_rows_referencing_unknown_products_are_rejected():
    preview = import_fixture(
        "unknown.xlsx", excel_fixtures.unknown_products_workbook()
    )

    pricing = dataset(preview, DatasetKind.PRICING).result
    quotations = dataset(preview, DatasetKind.HISTORICAL_QUOTATION).result

    assert len(pricing.rejected_rows) == 1
    assert len(quotations.rejected_rows) == 1
    assert IssueCode.MISSING_PRODUCT_REFERENCE.value in issue_codes(preview)
    assert all(
        row.values["product_id"] in {"SYN-100", "SYN-200", "SYN-300"}
        for row in pricing.accepted_rows
    )


def test_invalid_dates_quantities_and_duplicate_quotations_are_rejected():
    preview = import_fixture(
        "quotations.xlsx", excel_fixtures.invalid_quotations_workbook()
    )

    quotations = dataset(preview, DatasetKind.HISTORICAL_QUOTATION).result
    codes = issue_codes(preview)

    assert len(quotations.valid_rows) == 1
    assert len(quotations.rejected_rows) == 3
    assert IssueCode.INVALID_DATE.value in codes
    assert IssueCode.NON_POSITIVE_QUANTITY.value in codes
    assert IssueCode.DUPLICATE_ROW.value in codes


def test_inconsistent_units_for_one_product_are_rejected():
    preview = import_fixture(
        "units.xlsx", excel_fixtures.inconsistent_units_workbook()
    )

    assert IssueCode.INCONSISTENT_UNIT.value in issue_codes(preview)
    assert dataset(preview, DatasetKind.PRICING).result.counts["rejected"] == 1


def test_only_mapped_sheets_are_imported_from_a_multi_sheet_workbook():
    preview = import_fixture(
        "multi.xlsx", excel_fixtures.multi_sheet_workbook()
    )

    assert {staged.sheet_name for staged in preview.datasets} == {
        "Product Master",
        "Pricing",
    }


def test_rejected_rows_are_never_part_of_the_accepted_set():
    preview = import_fixture(
        "currencies.xlsx", excel_fixtures.mixed_currencies_workbook()
    )

    for staged in preview.datasets:
        accepted = {row.row_number for row in staged.result.accepted_rows}
        rejected = {row.row_number for row in staged.result.rejected_rows}
        assert not (accepted & rejected)


def test_validation_report_can_be_downloaded_as_csv_and_markdown():
    preview = import_fixture(
        "currencies.xlsx", excel_fixtures.mixed_currencies_workbook()
    )

    csv_report = render_validation_report_csv(preview)
    markdown_report = render_validation_report_markdown(preview)
    summary = json.loads(render_validation_summary_json(preview))

    assert csv_report.splitlines()[0].startswith("dataset,sheet,row_number")
    assert IssueCode.UNSUPPORTED_CURRENCY.value in csv_report
    assert "# Pricing data validation report" in markdown_report
    assert preview.workbook.content_hash in markdown_report
    assert summary["counts"]["rejected"] == 2
    assert summary["file_hash"] == preview.workbook.content_hash
