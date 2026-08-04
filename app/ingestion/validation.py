"""Deterministic row validation and quarantine.

Rows are separated into three buckets:

``valid``     — usable as-is,
``warning``   — usable but flagged for human attention,
``rejected``  — quarantined; never reaches the published dataset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from app.ingestion.config import IngestionConfig, load_ingestion_config
from app.ingestion.mapping import ColumnMappingProfile
from app.ingestion.normalization import NormalizationError, normalize_value
from app.ingestion.schemas import CanonicalSchema, DatasetKind, FieldKind


class Severity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class IssueCode(str, Enum):
    MISSING_REQUIRED_FIELD = "missing_required_field"
    DUPLICATE_PRODUCT_ID = "duplicate_product_id"
    DUPLICATE_ROW = "duplicate_row"
    INVALID_DATE = "invalid_date"
    INVALID_NUMBER = "invalid_number"
    NON_POSITIVE_QUANTITY = "non_positive_quantity"
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    INCONSISTENT_UNIT = "inconsistent_unit"
    MISSING_PRODUCT_REFERENCE = "missing_product_reference"
    MALFORMED_REGION = "malformed_region_code"
    EMPTY_PRICE = "empty_price"
    IMPOSSIBLE_PRICE_RELATIONSHIP = "impossible_price_relationship"
    IMPOSSIBLE_COST_RELATIONSHIP = "impossible_cost_relationship"
    INVALID_DATE_RANGE = "invalid_date_range"
    NORMALIZED_UNIT = "normalized_unit"


@dataclass(frozen=True)
class RowIssue:
    code: IssueCode
    severity: Severity
    message: str
    field_name: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "field": self.field_name,
            "message": self.message,
        }


@dataclass(frozen=True)
class ValidatedRow:
    row_number: int
    dataset_kind: DatasetKind
    values: Mapping[str, Any]
    raw_values: Mapping[str, Any]
    issues: tuple[RowIssue, ...] = ()

    @property
    def rejected(self) -> bool:
        return any(issue.severity is Severity.ERROR for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity is Severity.WARNING for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "dataset_kind": self.dataset_kind.value,
            "values": {
                name: _jsonable(value) for name, value in self.values.items()
            },
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class DatasetValidationResult:
    dataset_kind: DatasetKind
    sheet_name: str
    valid_rows: tuple[ValidatedRow, ...] = ()
    warning_rows: tuple[ValidatedRow, ...] = ()
    rejected_rows: tuple[ValidatedRow, ...] = ()
    dataset_issues: tuple[RowIssue, ...] = ()

    @property
    def accepted_rows(self) -> tuple[ValidatedRow, ...]:
        """Rows that may be published: valid plus warning rows."""

        return tuple(
            sorted(
                self.valid_rows + self.warning_rows,
                key=lambda row: row.row_number,
            )
        )

    @property
    def counts(self) -> dict[str, int]:
        return {
            "valid": len(self.valid_rows),
            "warning": len(self.warning_rows),
            "rejected": len(self.rejected_rows),
            "total": len(self.valid_rows)
            + len(self.warning_rows)
            + len(self.rejected_rows),
        }


def validate_rows(
    rows: Sequence[Mapping[str, Any]],
    profile: ColumnMappingProfile,
    *,
    row_numbers: Sequence[int] | None = None,
    known_product_ids: Iterable[str] | None = None,
    config: IngestionConfig | None = None,
) -> DatasetValidationResult:
    """Normalise and validate raw mapped rows for one dataset."""

    resolved = config or load_ingestion_config()
    schema = profile.schema
    region_pattern = re.compile(resolved.region_pattern)
    product_ids = (
        {str(item) for item in known_product_ids}
        if known_product_ids is not None
        else None
    )

    validated: list[ValidatedRow] = []
    seen_keys: dict[tuple[Any, ...], int] = {}
    seen_product_ids: dict[str, int] = {}
    units_by_product: dict[str, str] = {}

    for index, raw_row in enumerate(rows):
        row_number = (
            row_numbers[index]
            if row_numbers is not None and index < len(row_numbers)
            else index + 1
        )
        values, issues = _normalize_row(raw_row, schema, config=resolved)

        _check_required(values, schema, issues)
        _check_currency(values, schema, resolved, issues)
        _check_region(values, schema, region_pattern, issues)
        _check_quantity(values, schema, issues)
        _check_prices(values, schema, issues)
        _check_dates(values, schema, issues)
        _check_product_reference(values, schema, product_ids, issues)
        _check_unit_consistency(values, schema, units_by_product, issues)
        _check_duplicates(
            values,
            schema,
            seen_keys,
            seen_product_ids,
            row_number,
            issues,
        )

        validated.append(
            ValidatedRow(
                row_number=row_number,
                dataset_kind=schema.kind,
                values=values,
                raw_values=dict(raw_row),
                issues=tuple(issues),
            )
        )

    rejected = tuple(row for row in validated if row.rejected)
    warning = tuple(
        row for row in validated if not row.rejected and row.has_warnings
    )
    valid = tuple(
        row for row in validated if not row.rejected and not row.has_warnings
    )
    return DatasetValidationResult(
        dataset_kind=schema.kind,
        sheet_name=profile.sheet_name,
        valid_rows=valid,
        warning_rows=warning,
        rejected_rows=rejected,
    )


def _normalize_row(
    raw_row: Mapping[str, Any],
    schema: CanonicalSchema,
    *,
    config: IngestionConfig,
) -> tuple[dict[str, Any], list[RowIssue]]:
    values: dict[str, Any] = {}
    issues: list[RowIssue] = []
    for canonical in schema.fields:
        if canonical.name not in raw_row:
            continue
        try:
            normalized = normalize_value(
                raw_row[canonical.name],
                canonical.kind,
                config=config,
            )
        except NormalizationError as error:
            values[canonical.name] = None
            issues.append(
                RowIssue(
                    code=(
                        IssueCode.INVALID_DATE
                        if canonical.kind is FieldKind.DATE
                        else IssueCode.INVALID_NUMBER
                    ),
                    severity=Severity.ERROR,
                    message=f"{canonical.name}: {error}",
                    field_name=canonical.name,
                )
            )
            continue
        values[canonical.name] = normalized.value
        for warning in normalized.warnings:
            issues.append(
                RowIssue(
                    code=IssueCode.NORMALIZED_UNIT,
                    severity=Severity.WARNING,
                    message=warning,
                    field_name=canonical.name,
                )
            )
    return values, issues


def _check_required(
    values: Mapping[str, Any],
    schema: CanonicalSchema,
    issues: list[RowIssue],
) -> None:
    for name in schema.required_fields:
        value = values.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            code = (
                IssueCode.EMPTY_PRICE
                if name.endswith("_price") or name == "amount"
                else IssueCode.MISSING_REQUIRED_FIELD
            )
            issues.append(
                RowIssue(
                    code=code,
                    severity=Severity.ERROR,
                    message=f"Required field {name!r} is empty.",
                    field_name=name,
                )
            )


def _check_currency(
    values: Mapping[str, Any],
    schema: CanonicalSchema,
    config: IngestionConfig,
    issues: list[RowIssue],
) -> None:
    if "currency" not in schema.field_names:
        return
    currency = values.get("currency")
    if not currency:
        return
    if not config.is_supported_currency(str(currency)):
        supported = ", ".join(config.supported_currencies)
        issues.append(
            RowIssue(
                code=IssueCode.UNSUPPORTED_CURRENCY,
                severity=Severity.ERROR,
                message=(
                    f"Currency {currency!r} is not supported. "
                    f"Supported currencies: {supported}."
                ),
                field_name="currency",
            )
        )


def _check_region(
    values: Mapping[str, Any],
    schema: CanonicalSchema,
    pattern: re.Pattern[str],
    issues: list[RowIssue],
) -> None:
    if "region" not in schema.field_names:
        return
    region = values.get("region")
    if not region:
        return
    if not pattern.match(str(region)):
        issues.append(
            RowIssue(
                code=IssueCode.MALFORMED_REGION,
                severity=Severity.ERROR,
                message=f"Region code {region!r} is malformed.",
                field_name="region",
            )
        )


def _check_quantity(
    values: Mapping[str, Any],
    schema: CanonicalSchema,
    issues: list[RowIssue],
) -> None:
    if "quantity" not in schema.field_names:
        return
    quantity = values.get("quantity")
    if quantity is None:
        return
    if quantity <= 0:
        issues.append(
            RowIssue(
                code=IssueCode.NON_POSITIVE_QUANTITY,
                severity=Severity.ERROR,
                message=f"Quantity {quantity} must be greater than zero.",
                field_name="quantity",
            )
        )


def _check_prices(
    values: Mapping[str, Any],
    schema: CanonicalSchema,
    issues: list[RowIssue],
) -> None:
    names = set(schema.field_names)
    list_price = _as_decimal(values.get("list_price")) if "list_price" in names else None
    net_price = _as_decimal(values.get("net_price")) if "net_price" in names else None
    minimum_price = (
        _as_decimal(values.get("minimum_price")) if "minimum_price" in names else None
    )
    cogs = _as_decimal(values.get("cogs")) if "cogs" in names else None
    amount = _as_decimal(values.get("amount")) if "amount" in names else None

    for name, value in (
        ("list_price", list_price),
        ("net_price", net_price),
        ("minimum_price", minimum_price),
        ("cogs", cogs),
        ("amount", amount),
    ):
        if value is not None and value < 0:
            issues.append(
                RowIssue(
                    code=IssueCode.INVALID_NUMBER,
                    severity=Severity.ERROR,
                    message=f"{name} must not be negative (found {value}).",
                    field_name=name,
                )
            )

    if list_price is not None and net_price is not None and net_price > list_price:
        issues.append(
            RowIssue(
                code=IssueCode.IMPOSSIBLE_PRICE_RELATIONSHIP,
                severity=Severity.ERROR,
                message=(
                    f"Net price {net_price} exceeds list price {list_price}."
                ),
                field_name="net_price",
            )
        )
    if (
        minimum_price is not None
        and net_price is not None
        and net_price < minimum_price
    ):
        issues.append(
            RowIssue(
                code=IssueCode.IMPOSSIBLE_PRICE_RELATIONSHIP,
                severity=Severity.ERROR,
                message=(
                    f"Net price {net_price} is below the minimum price "
                    f"{minimum_price}."
                ),
                field_name="net_price",
            )
        )
    if cogs is not None and net_price is not None and cogs > net_price:
        issues.append(
            RowIssue(
                code=IssueCode.IMPOSSIBLE_COST_RELATIONSHIP,
                severity=Severity.WARNING,
                message=(
                    f"Cost of goods sold {cogs} exceeds net price {net_price}; "
                    "this row would price below cost."
                ),
                field_name="cogs",
            )
        )


def _check_dates(
    values: Mapping[str, Any],
    schema: CanonicalSchema,
    issues: list[RowIssue],
) -> None:
    names = set(schema.field_names)
    if {"valid_from", "valid_to"} <= names:
        valid_from = values.get("valid_from")
        valid_to = values.get("valid_to")
        if valid_from and valid_to and valid_to < valid_from:
            issues.append(
                RowIssue(
                    code=IssueCode.INVALID_DATE_RANGE,
                    severity=Severity.ERROR,
                    message=(
                        f"valid_to {valid_to} precedes valid_from {valid_from}."
                    ),
                    field_name="valid_to",
                )
            )


def _check_product_reference(
    values: Mapping[str, Any],
    schema: CanonicalSchema,
    known_product_ids: set[str] | None,
    issues: list[RowIssue],
) -> None:
    if known_product_ids is None or not schema.product_reference:
        return
    for name in (schema.product_reference, "compatible_product_id"):
        if name not in schema.field_names:
            continue
        product_id = values.get(name)
        if not product_id:
            continue
        if str(product_id) not in known_product_ids:
            issues.append(
                RowIssue(
                    code=IssueCode.MISSING_PRODUCT_REFERENCE,
                    severity=Severity.ERROR,
                    message=(
                        f"Product {product_id!r} is not present in the product "
                        "master dataset."
                    ),
                    field_name=name,
                )
            )


def _check_unit_consistency(
    values: Mapping[str, Any],
    schema: CanonicalSchema,
    units_by_product: dict[str, str],
    issues: list[RowIssue],
) -> None:
    if "unit_of_measure" not in schema.field_names:
        return
    product_id = values.get("product_id")
    unit = values.get("unit_of_measure")
    if not product_id or not unit:
        return
    key = str(product_id)
    previous = units_by_product.setdefault(key, str(unit))
    if previous != str(unit):
        issues.append(
            RowIssue(
                code=IssueCode.INCONSISTENT_UNIT,
                severity=Severity.ERROR,
                message=(
                    f"Product {key!r} uses unit {unit!r} here but {previous!r} "
                    "in an earlier row."
                ),
                field_name="unit_of_measure",
            )
        )


def _check_duplicates(
    values: Mapping[str, Any],
    schema: CanonicalSchema,
    seen_keys: dict[tuple[Any, ...], int],
    seen_product_ids: dict[str, int],
    row_number: int,
    issues: list[RowIssue],
) -> None:
    if schema.unique_key:
        key = tuple(_jsonable(values.get(name)) for name in schema.unique_key)
        if all(part not in (None, "") for part in key):
            previous = seen_keys.get(key)
            if previous is not None:
                duplicate_code = (
                    IssueCode.DUPLICATE_PRODUCT_ID
                    if schema.kind is DatasetKind.PRODUCT_MASTER
                    else IssueCode.DUPLICATE_ROW
                )
                label = ", ".join(
                    f"{name}={value}"
                    for name, value in zip(schema.unique_key, key)
                )
                issues.append(
                    RowIssue(
                        code=duplicate_code,
                        severity=Severity.ERROR,
                        message=(
                            f"Duplicate of row {previous} ({label})."
                        ),
                        field_name=schema.unique_key[0],
                    )
                )
            else:
                seen_keys[key] = row_number

    if schema.kind is DatasetKind.PRODUCT_MASTER:
        product_id = values.get("product_id")
        if product_id:
            seen_product_ids.setdefault(str(product_id), row_number)


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
