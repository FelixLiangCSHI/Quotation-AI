"""The offline SAP Excel ingestion pipeline.

    upload
    -> file validation
    -> sheet selection
    -> column mapping
    -> normalisation
    -> row validation
    -> error quarantine
    -> user confirmation
    -> publication as a PricingDataVersion

The pipeline stops at the staged preview. Publication is a separate, explicit
action in :mod:`app.ingestion.repository`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.ingestion.config import IngestionConfig, load_ingestion_config
from app.ingestion.mapping import (
    ColumnMappingProfile,
    MappingError,
    build_profile,
    normalize_header,
)
from app.ingestion.schemas import DatasetKind
from app.ingestion.storage import WorkbookStorage
from app.ingestion.validation import DatasetValidationResult, validate_rows
from app.ingestion.workbook import (
    SheetPreview,
    WorkbookFile,
    list_sheets,
    read_sheet,
    validate_workbook_file,
)


class IngestionError(Exception):
    """Raised when an import cannot proceed."""


@dataclass(frozen=True)
class DatasetPlan:
    """A user's decision about one sheet: which dataset, mapped how."""

    dataset_kind: DatasetKind
    profile: ColumnMappingProfile


@dataclass(frozen=True)
class StagedDataset:
    dataset_kind: DatasetKind
    sheet_name: str
    profile: ColumnMappingProfile
    result: DatasetValidationResult


@dataclass(frozen=True)
class ImportPreview:
    """The reviewable outcome of an import. Nothing has been published yet."""

    workbook: WorkbookFile
    datasets: tuple[StagedDataset, ...]
    storage_uri: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        totals = {"valid": 0, "warning": 0, "rejected": 0, "total": 0}
        for dataset in self.datasets:
            for key, value in dataset.result.counts.items():
                totals[key] += value
        return totals

    @property
    def has_rejections(self) -> bool:
        return self.counts["rejected"] > 0

    def summary(self) -> dict[str, Any]:
        return {
            "source_filename": self.workbook.filename,
            "file_hash": self.workbook.content_hash,
            "size_bytes": self.workbook.size_bytes,
            "storage_uri": self.storage_uri,
            "counts": self.counts,
            "warnings": list(self.warnings),
            "datasets": [
                {
                    "dataset_kind": dataset.dataset_kind.value,
                    "sheet_name": dataset.sheet_name,
                    "counts": dataset.result.counts,
                    "mapping_profile": dataset.profile.to_dict(),
                }
                for dataset in self.datasets
            ],
        }


@dataclass
class ImportSession:
    """Stateful helper driving the interactive (Streamlit) import flow."""

    workbook: WorkbookFile
    config: IngestionConfig = field(default_factory=load_ingestion_config)
    storage_uri: str = ""

    @property
    def sheet_names(self) -> tuple[str, ...]:
        return list_sheets(self.workbook)

    def preview_sheet(
        self,
        sheet_name: str,
        *,
        header_row: int | None = None,
        max_rows: int = 25,
    ) -> SheetPreview:
        return read_sheet(
            self.workbook,
            sheet_name,
            header_row=header_row,
            max_rows=max_rows,
        )

    def suggest_profile(
        self,
        sheet_name: str,
        dataset_kind: DatasetKind | str,
        *,
        header_row: int | None = None,
        overrides: Mapping[str, str] | None = None,
        extra_aliases: Mapping[str, str] | None = None,
        name: str = "",
    ) -> ColumnMappingProfile:
        preview = self.preview_sheet(sheet_name, header_row=header_row, max_rows=1)
        kind = DatasetKind(dataset_kind)
        return build_profile(
            name=name or f"{kind.value}:{sheet_name}",
            dataset_kind=kind,
            sheet_name=sheet_name,
            headers=preview.headers,
            header_row=preview.header_row,
            overrides=overrides,
            extra_aliases=extra_aliases,
        )


def start_import(
    filename: str,
    payload: bytes,
    *,
    config: IngestionConfig | None = None,
    storage: WorkbookStorage | None = None,
) -> ImportSession:
    """Validate an upload and, if storage is configured, retain the raw bytes."""

    resolved = config or load_ingestion_config()
    workbook = validate_workbook_file(filename, payload, config=resolved)
    storage_uri = ""
    if storage is not None:
        storage_uri = storage.store(
            content_hash=workbook.content_hash,
            filename=workbook.filename,
            payload=payload,
        )
    return ImportSession(
        workbook=workbook,
        config=resolved,
        storage_uri=storage_uri,
    )


def run_import(
    session: ImportSession,
    plans: Sequence[DatasetPlan],
) -> ImportPreview:
    """Normalise and validate every planned dataset in the workbook."""

    if not plans:
        raise IngestionError("At least one sheet must be mapped before import.")

    resolved_plans = _order_plans(plans)
    known_product_ids: set[str] | None = None
    staged: list[StagedDataset] = []

    for plan in resolved_plans:
        plan.profile.validate()
        rows, row_numbers = extract_mapped_rows(session.workbook, plan.profile)
        result = validate_rows(
            rows,
            plan.profile,
            row_numbers=row_numbers,
            known_product_ids=known_product_ids,
            config=session.config,
        )
        if plan.dataset_kind is DatasetKind.PRODUCT_MASTER:
            known_product_ids = {
                str(row.values.get("product_id"))
                for row in result.accepted_rows
                if row.values.get("product_id")
            }
        staged.append(
            StagedDataset(
                dataset_kind=plan.dataset_kind,
                sheet_name=plan.profile.sheet_name,
                profile=plan.profile,
                result=result,
            )
        )

    warnings = list(session.workbook.warnings)
    if known_product_ids is None:
        warnings.append(
            "No product master sheet was mapped, so product references could "
            "not be checked."
        )
    return ImportPreview(
        workbook=session.workbook,
        datasets=tuple(staged),
        storage_uri=session.storage_uri,
        warnings=tuple(warnings),
    )


def _order_plans(plans: Sequence[DatasetPlan]) -> tuple[DatasetPlan, ...]:
    """Product master first so later datasets can check references."""

    return tuple(
        sorted(
            plans,
            key=lambda plan: 0
            if plan.dataset_kind is DatasetKind.PRODUCT_MASTER
            else 1,
        )
    )


def extract_mapped_rows(
    workbook: WorkbookFile,
    profile: ColumnMappingProfile,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Return the mapped raw values and the worksheet row number of each row."""

    preview = read_sheet(
        workbook,
        profile.sheet_name,
        header_row=profile.header_row,
    )
    if not preview.headers:
        raise IngestionError(
            f"Sheet {profile.sheet_name!r} has no readable header row."
        )

    header_index: dict[str, int] = {}
    for index, header in enumerate(preview.headers):
        key = normalize_header(header)
        if key:
            header_index.setdefault(key, index)

    column_for_field: dict[str, int] = {}
    for canonical_name, header in profile.field_to_header.items():
        index = header_index.get(normalize_header(header))
        if index is None:
            raise MappingError(
                f"Column {header!r} mapped to {canonical_name!r} is not present "
                f"in sheet {profile.sheet_name!r}."
            )
        column_for_field[canonical_name] = index

    rows = [
        {
            name: raw_row[index] if index < len(raw_row) else None
            for name, index in column_for_field.items()
        }
        for raw_row in preview.rows
    ]
    return rows, list(preview.row_numbers)
