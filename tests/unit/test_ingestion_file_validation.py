"""File-validation tests for offline SAP workbook uploads."""

from __future__ import annotations

import pytest

from app.ingestion.config import IngestionConfig
from app.ingestion.workbook import (
    CorruptWorkbookError,
    ProtectedWorkbookError,
    UnsupportedWorkbookError,
    list_sheets,
    read_sheet,
    validate_workbook_file,
)
from tests.fixtures import excel_fixtures


def test_valid_xlsx_upload_is_accepted():
    payload = excel_fixtures.valid_workbook()

    workbook = validate_workbook_file("SAP export.xlsx", payload)

    assert workbook.filename == "SAP export.xlsx"
    assert workbook.extension == ".xlsx"
    assert workbook.size_bytes == len(payload)
    assert len(workbook.content_hash) == 64
    assert not workbook.macro_enabled
    assert workbook.warnings == ()


def test_xlsm_upload_is_accepted_with_a_macro_warning():
    workbook = validate_workbook_file(
        "SAP export.xlsm", excel_fixtures.valid_workbook()
    )

    assert workbook.macro_enabled
    assert workbook.warnings
    assert "macro" in workbook.warnings[0].casefold()


@pytest.mark.parametrize("filename", ["export.xls", "export.csv", "export.txt"])
def test_unsupported_extensions_are_rejected(filename):
    with pytest.raises(UnsupportedWorkbookError) as error:
        validate_workbook_file(filename, excel_fixtures.valid_workbook())

    assert ".xlsx" in str(error.value)


def test_password_protected_workbook_is_rejected_with_a_clear_message():
    with pytest.raises(ProtectedWorkbookError) as error:
        validate_workbook_file(
            "protected.xlsx", excel_fixtures.password_protected_workbook()
        )

    assert "password" in str(error.value).casefold()


def test_a_renamed_non_workbook_is_rejected():
    with pytest.raises(CorruptWorkbookError):
        validate_workbook_file("fake.xlsx", excel_fixtures.not_a_workbook())


def test_empty_upload_is_rejected():
    with pytest.raises(CorruptWorkbookError):
        validate_workbook_file("empty.xlsx", b"")


def test_oversized_upload_is_rejected():
    payload = excel_fixtures.valid_workbook()
    config = IngestionConfig(max_upload_bytes=10)

    with pytest.raises(UnsupportedWorkbookError) as error:
        validate_workbook_file("big.xlsx", payload, config=config)

    assert "upload limit" in str(error.value)


def test_sheet_selection_lists_every_sheet():
    workbook = validate_workbook_file(
        "multi.xlsx", excel_fixtures.multi_sheet_workbook()
    )

    assert list_sheets(workbook) == (
        "Cover",
        "Product Master",
        "Pricing",
        "Notes",
    )


def test_reading_a_sheet_returns_headers_and_numbered_rows():
    workbook = validate_workbook_file(
        "valid.xlsx", excel_fixtures.valid_workbook()
    )

    preview = read_sheet(workbook, "Pricing")

    assert preview.header_row == 1
    assert preview.headers[:2] == ("Cat#", "Description")
    assert preview.total_rows == 3
    assert preview.row_numbers == (2, 3, 4)


def test_reading_an_unknown_sheet_fails():
    workbook = validate_workbook_file(
        "valid.xlsx", excel_fixtures.valid_workbook()
    )

    with pytest.raises(Exception):
        read_sheet(workbook, "Does Not Exist")
