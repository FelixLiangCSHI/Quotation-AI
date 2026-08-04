"""Synthetic SAP-like Excel fixtures.

These workbooks contain invented data only. They are generated at test time so
no binary artefact is committed, and they can also be written to disk for a
manual demo via ``python -m tests.fixtures.excel_fixtures <directory>``.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from openpyxl import Workbook

PRODUCT_HEADERS = (
    "Material",
    "Material Description",
    "Prod Family",
    "Prod Type",
    "Base Unit",
    "Region",
    "Status",
)

PRICING_HEADERS = (
    "Cat#",
    "Description",
    "Prod Family",
    "List Price",
    "Net Price",
    "Minimum Price",
    "COGS",
    "Currency",
    "Region",
    "UOM",
    "Valid From",
    "Valid To",
)

QUOTATION_HEADERS = (
    "Quotation ID",
    "Material",
    "Customer",
    "Quotation Date",
    "Quantity",
    "Net Price",
    "Currency",
    "Region",
    "Outcome",
)

COMPATIBILITY_HEADERS = (
    "Main Product",
    "Compatible Product",
    "Relation Type",
    "Region",
    "Notes",
)

COST_HEADERS = (
    "Material",
    "Cost Element",
    "Cost Component Name",
    "Amount",
    "Currency",
    "Base Unit",
)

PRODUCT_ROWS: tuple[tuple[Any, ...], ...] = (
    ("SYN-100", "Synthetic imaging system", "FMT", "System", "EA", "US", "Active"),
    ("SYN-200", "Synthetic detector panel", "DET", "Option", "EA", "US", "Active"),
    ("SYN-300", "Synthetic mobile console", "FMT", "System", "EA", "EU", "Active"),
)

PRICING_ROWS: tuple[tuple[Any, ...], ...] = (
    ("SYN-100", "Synthetic imaging system", "FMT", 120000, 100000, 80000, 60000,
     "USD", "US", "EA", date(2026, 1, 1), date(2026, 12, 31)),
    ("SYN-200", "Synthetic detector panel", "DET", 30000, 25000, 20000, 15000,
     "USD", "US", "EA", date(2026, 1, 1), date(2026, 12, 31)),
    ("SYN-300", "Synthetic mobile console", "FMT", 90000, 78000, 60000, 50000,
     "EUR", "EU", "EA", date(2026, 1, 1), date(2026, 12, 31)),
)

QUOTATION_ROWS: tuple[tuple[Any, ...], ...] = (
    ("Q-1001", "SYN-100", "Synthetic Hospital A", date(2026, 2, 3), 1, 98000,
     "USD", "US", "won"),
    ("Q-1002", "SYN-200", "Synthetic Hospital B", date(2026, 2, 14), 2, 24000,
     "USD", "US", "lost"),
    ("Q-1003", "SYN-300", "Synthetic Clinic C", date(2026, 3, 1), 1, 77000,
     "EUR", "EU", "won"),
)

COMPATIBILITY_ROWS: tuple[tuple[Any, ...], ...] = (
    ("SYN-100", "SYN-200", "supported_option", "US", "Synthetic pairing"),
    ("SYN-300", "SYN-200", "supported_option", "EU", "Synthetic pairing"),
)

COST_ROWS: tuple[tuple[Any, ...], ...] = (
    ("SYN-100", "MAT", "Material cost", 45000, "USD", "EA"),
    ("SYN-100", "LAB", "Labour cost", 15000, "USD", "EA"),
    ("SYN-200", "MAT", "Material cost", 15000, "USD", "EA"),
)


def _build(
    sheets: Mapping[str, tuple[Sequence[str], Sequence[Sequence[Any]]]],
) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, (headers, rows) in sheets.items():
        worksheet = workbook.create_sheet(title=title)
        worksheet.append(list(headers))
        for row in rows:
            worksheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def valid_workbook() -> bytes:
    """A clean, fully mappable multi-sheet workbook."""

    return _build(
        {
            "Product Master": (PRODUCT_HEADERS, PRODUCT_ROWS),
            "Pricing": (PRICING_HEADERS, PRICING_ROWS),
            "Quotations": (QUOTATION_HEADERS, QUOTATION_ROWS),
            "Compatibility": (COMPATIBILITY_HEADERS, COMPATIBILITY_ROWS),
            "Cost Components": (COST_HEADERS, COST_ROWS),
        }
    )


def multi_sheet_workbook() -> bytes:
    """The valid workbook plus unrelated sheets the user must not select."""

    return _build(
        {
            "Cover": (
                ("Report", "Value"),
                (("Synthetic export", "demo"), ("Generated for tests", "demo")),
            ),
            "Product Master": (PRODUCT_HEADERS, PRODUCT_ROWS),
            "Pricing": (PRICING_HEADERS, PRICING_ROWS),
            "Notes": (("Note", "Detail"), (("No pricing data here", "n/a"),)),
        }
    )


def missing_columns_workbook() -> bytes:
    """Pricing sheet without the required ``Net Price`` and ``Currency``."""

    headers = tuple(
        header
        for header in PRICING_HEADERS
        if header not in {"Net Price", "Currency"}
    )
    keep = [PRICING_HEADERS.index(header) for header in headers]
    rows = tuple(tuple(row[index] for index in keep) for row in PRICING_ROWS)
    return _build(
        {
            "Product Master": (PRODUCT_HEADERS, PRODUCT_ROWS),
            "Pricing": (headers, rows),
        }
    )


def duplicate_rows_workbook() -> bytes:
    """A duplicated product master row and a duplicated pricing key."""

    products = PRODUCT_ROWS + (PRODUCT_ROWS[0],)
    pricing = PRICING_ROWS + (PRICING_ROWS[0],)
    return _build(
        {
            "Product Master": (PRODUCT_HEADERS, products),
            "Pricing": (PRICING_HEADERS, pricing),
        }
    )


def malformed_prices_workbook() -> bytes:
    """Non-numeric, empty and impossible price relationships."""

    rows = (
        ("SYN-100", "Synthetic imaging system", "FMT", "n/a", "not-a-number",
         80000, 60000, "USD", "US", "EA", date(2026, 1, 1), date(2026, 12, 31)),
        ("SYN-200", "Synthetic detector panel", "DET", 30000, None, 20000,
         15000, "USD", "US", "EA", date(2026, 1, 1), date(2026, 12, 31)),
        ("SYN-300", "Synthetic mobile console", "FMT", 50000, 90000, 60000,
         95000, "USD", "US", "EA", date(2026, 1, 1), date(2026, 12, 31)),
    )
    return _build(
        {
            "Product Master": (PRODUCT_HEADERS, PRODUCT_ROWS),
            "Pricing": (PRICING_HEADERS, rows),
        }
    )


def mixed_currencies_workbook() -> bytes:
    """One supported currency, one unsupported, one malformed region."""

    rows = (
        ("SYN-100", "Synthetic imaging system", "FMT", 120000, 100000, 80000,
         60000, "USD", "US", "EA", date(2026, 1, 1), date(2026, 12, 31)),
        ("SYN-200", "Synthetic detector panel", "DET", 30000, 25000, 20000,
         15000, "XYZ", "US", "EA", date(2026, 1, 1), date(2026, 12, 31)),
        ("SYN-300", "Synthetic mobile console", "FMT", 90000, 78000, 60000,
         50000, "EUR", "Europe-Wide-Region", "EA", date(2026, 1, 1),
         date(2026, 12, 31)),
    )
    return _build(
        {
            "Product Master": (PRODUCT_HEADERS, PRODUCT_ROWS),
            "Pricing": (PRICING_HEADERS, rows),
        }
    )


def unknown_products_workbook() -> bytes:
    """Quotation and pricing rows referencing products not in the master."""

    pricing = PRICING_ROWS + (
        ("SYN-999", "Unknown synthetic product", "FMT", 10000, 9000, 8000, 7000,
         "USD", "US", "EA", date(2026, 1, 1), date(2026, 12, 31)),
    )
    quotations = QUOTATION_ROWS + (
        ("Q-1004", "SYN-888", "Synthetic Hospital D", date(2026, 3, 15), 1,
         5000, "USD", "US", "won"),
    )
    return _build(
        {
            "Product Master": (PRODUCT_HEADERS, PRODUCT_ROWS),
            "Pricing": (PRICING_HEADERS, pricing),
            "Quotations": (QUOTATION_HEADERS, quotations),
        }
    )


def invalid_quotations_workbook() -> bytes:
    """Invalid dates, non-positive quantities and duplicate quotation rows."""

    rows = (
        ("Q-1001", "SYN-100", "Synthetic Hospital A", date(2026, 2, 3), 1,
         98000, "USD", "US", "won"),
        ("Q-1001", "SYN-100", "Synthetic Hospital A", date(2026, 2, 3), 1,
         98000, "USD", "US", "won"),
        ("Q-1002", "SYN-200", "Synthetic Hospital B", "31/02/2026", 2, 24000,
         "USD", "US", "lost"),
        ("Q-1003", "SYN-300", "Synthetic Clinic C", date(2026, 3, 1), 0, 77000,
         "EUR", "EU", "won"),
    )
    return _build(
        {
            "Product Master": (PRODUCT_HEADERS, PRODUCT_ROWS),
            "Quotations": (QUOTATION_HEADERS, rows),
        }
    )


def inconsistent_units_workbook() -> bytes:
    """The same product priced in two different units of measure."""

    rows = (
        ("SYN-100", "Synthetic imaging system", "FMT", 120000, 100000, 80000,
         60000, "USD", "US", "EA", date(2026, 1, 1), date(2026, 12, 31)),
        ("SYN-100", "Synthetic imaging system", "FMT", 120000, 100000, 80000,
         60000, "USD", "EU", "SET", date(2026, 1, 1), date(2026, 12, 31)),
    )
    return _build(
        {
            "Product Master": (PRODUCT_HEADERS, PRODUCT_ROWS),
            "Pricing": (PRICING_HEADERS, rows),
        }
    )


def not_a_workbook() -> bytes:
    return b"This is a CSV,not a workbook\n1,2\n"


def password_protected_workbook() -> bytes:
    """An OLE2 container, which is how Excel stores an encrypted workbook."""

    return b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512


FIXTURES = {
    "valid_workbook.xlsx": valid_workbook,
    "multi_sheet.xlsx": multi_sheet_workbook,
    "missing_columns.xlsx": missing_columns_workbook,
    "duplicate_rows.xlsx": duplicate_rows_workbook,
    "malformed_prices.xlsx": malformed_prices_workbook,
    "mixed_currencies.xlsx": mixed_currencies_workbook,
    "unknown_products.xlsx": unknown_products_workbook,
    "invalid_quotations.xlsx": invalid_quotations_workbook,
    "inconsistent_units.xlsx": inconsistent_units_workbook,
}


def write_all(directory: Path) -> tuple[Path, ...]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, builder in FIXTURES.items():
        target = directory / filename
        target.write_bytes(builder())
        written.append(target)
    return tuple(written)


if __name__ == "__main__":  # pragma: no cover - manual demo helper
    import sys

    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "./var/fixtures")
    for path in write_all(destination):
        print(path)
