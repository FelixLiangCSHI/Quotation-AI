"""Offline SAP/Excel ingestion.

No live SAP connectivity exists anywhere in this package. The only input is an
offline export that an authorised internal user uploads.
"""

from __future__ import annotations

from app.ingestion.config import IngestionConfig, load_ingestion_config
from app.ingestion.mapping import (
    ColumnMappingProfile,
    MappingError,
    build_profile,
    suggest_mapping,
)
from app.ingestion.normalization import NormalizationError, normalize_value
from app.ingestion.pipeline import (
    DatasetPlan,
    ImportPreview,
    ImportSession,
    IngestionError,
    StagedDataset,
    run_import,
    start_import,
)
from app.ingestion.report import (
    render_validation_report_csv,
    render_validation_report_markdown,
    render_validation_summary_json,
)
from app.ingestion.repository import (
    DuplicateImportError,
    PricingDataRepository,
    PricingDataRepositoryError,
    PricingDataVersionNotFoundError,
    PricingDataVersionSummary,
)
from app.ingestion.schemas import (
    CANONICAL_SCHEMAS,
    CanonicalSchema,
    DatasetKind,
    FieldKind,
    get_schema,
)
from app.ingestion.storage import (
    InMemoryWorkbookStorage,
    LocalWorkbookStorage,
    WorkbookStorage,
    file_hash,
)
from app.ingestion.validation import (
    DatasetValidationResult,
    IssueCode,
    RowIssue,
    Severity,
    ValidatedRow,
    validate_rows,
)
from app.ingestion.workbook import (
    CorruptWorkbookError,
    ProtectedWorkbookError,
    SheetPreview,
    UnsupportedWorkbookError,
    WorkbookFile,
    WorkbookValidationError,
    list_sheets,
    read_sheet,
    validate_workbook_file,
)

__all__ = [
    "CANONICAL_SCHEMAS",
    "CanonicalSchema",
    "ColumnMappingProfile",
    "CorruptWorkbookError",
    "DatasetKind",
    "DatasetPlan",
    "DatasetValidationResult",
    "DuplicateImportError",
    "FieldKind",
    "ImportPreview",
    "ImportSession",
    "InMemoryWorkbookStorage",
    "IngestionConfig",
    "IngestionError",
    "IssueCode",
    "LocalWorkbookStorage",
    "MappingError",
    "NormalizationError",
    "PricingDataRepository",
    "PricingDataRepositoryError",
    "PricingDataVersionNotFoundError",
    "PricingDataVersionSummary",
    "ProtectedWorkbookError",
    "RowIssue",
    "Severity",
    "SheetPreview",
    "StagedDataset",
    "UnsupportedWorkbookError",
    "ValidatedRow",
    "WorkbookFile",
    "WorkbookStorage",
    "WorkbookValidationError",
    "build_profile",
    "file_hash",
    "get_schema",
    "list_sheets",
    "load_ingestion_config",
    "normalize_value",
    "read_sheet",
    "render_validation_report_csv",
    "render_validation_report_markdown",
    "render_validation_summary_json",
    "run_import",
    "start_import",
    "suggest_mapping",
    "validate_rows",
    "validate_workbook_file",
]
