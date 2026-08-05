"""Ingestion configuration.

All values are environment-overridable so a deployment can widen the allowed
currency list or point the raw-workbook store at a secure location without a
code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.ingestion.schemas import (
    DEFAULT_SUPPORTED_CURRENCIES,
    DEFAULT_UNIT_ALIASES,
)

#: Extensions the pipeline will attempt to read.
SUPPORTED_EXTENSIONS = (".xlsx", ".xlsm")

#: Default upload ceiling. Large SAP exports are rejected rather than silently
#: exhausting memory.
DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024

DEFAULT_STORAGE_ROOT = Path("./var/pricing_uploads")

#: Decompression guards. An Office Open XML workbook is a ZIP archive, so an
#: attacker can ship a small file that expands to gigabytes. These caps bound
#: the declared uncompressed size, the expansion ratio and the entry count.
DEFAULT_MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 200.0
DEFAULT_MAX_ARCHIVE_ENTRIES = 2048
#: Bound on the number of data rows read from any one sheet.
DEFAULT_MAX_SHEET_ROWS = 200_000

#: Region codes are ISO-3166 alpha-2/alpha-3 style or an approved market code.
DEFAULT_REGION_PATTERN = r"^[A-Z]{2,3}(-[A-Z0-9]{1,3})?$"


@dataclass(frozen=True)
class IngestionConfig:
    supported_currencies: tuple[str, ...] = DEFAULT_SUPPORTED_CURRENCIES
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    storage_root: Path = DEFAULT_STORAGE_ROOT
    region_pattern: str = DEFAULT_REGION_PATTERN
    unit_aliases: Mapping[str, str] = DEFAULT_UNIT_ALIASES
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO
    max_archive_entries: int = DEFAULT_MAX_ARCHIVE_ENTRIES
    max_sheet_rows: int = DEFAULT_MAX_SHEET_ROWS

    def is_supported_currency(self, code: str) -> bool:
        return code.upper() in self.supported_currencies


def load_ingestion_config(
    environment: Mapping[str, str] | None = None,
) -> IngestionConfig:
    values = os.environ if environment is None else environment

    raw_currencies = (values.get("INGESTION_SUPPORTED_CURRENCIES") or "").strip()
    currencies = (
        tuple(
            code.strip().upper()
            for code in raw_currencies.split(",")
            if code.strip()
        )
        or DEFAULT_SUPPORTED_CURRENCIES
    )

    raw_max = (values.get("INGESTION_MAX_UPLOAD_BYTES") or "").strip()
    try:
        max_bytes = int(raw_max) if raw_max else DEFAULT_MAX_UPLOAD_BYTES
    except ValueError as error:
        raise ValueError(
            "INGESTION_MAX_UPLOAD_BYTES must be an integer number of bytes"
        ) from error
    if max_bytes <= 0:
        raise ValueError("INGESTION_MAX_UPLOAD_BYTES must be positive")

    def _positive_int(name: str, default: int) -> int:
        raw = (values.get(name) or "").strip()
        try:
            parsed = int(raw) if raw else default
        except ValueError as error:
            raise ValueError(f"{name} must be an integer") from error
        if parsed <= 0:
            raise ValueError(f"{name} must be positive")
        return parsed

    def _positive_float(name: str, default: float) -> float:
        raw = (values.get(name) or "").strip()
        try:
            parsed = float(raw) if raw else default
        except ValueError as error:
            raise ValueError(f"{name} must be a number") from error
        if parsed <= 0:
            raise ValueError(f"{name} must be positive")
        return parsed

    max_uncompressed = _positive_int(
        "INGESTION_MAX_UNCOMPRESSED_BYTES", DEFAULT_MAX_UNCOMPRESSED_BYTES
    )
    max_ratio = _positive_float(
        "INGESTION_MAX_COMPRESSION_RATIO", DEFAULT_MAX_COMPRESSION_RATIO
    )
    max_entries = _positive_int(
        "INGESTION_MAX_ARCHIVE_ENTRIES", DEFAULT_MAX_ARCHIVE_ENTRIES
    )
    max_rows = _positive_int("INGESTION_MAX_SHEET_ROWS", DEFAULT_MAX_SHEET_ROWS)

    raw_root = (values.get("INGESTION_STORAGE_ROOT") or "").strip()
    storage_root = Path(raw_root) if raw_root else DEFAULT_STORAGE_ROOT

    return IngestionConfig(
        supported_currencies=currencies,
        max_upload_bytes=max_bytes,
        storage_root=storage_root,
        region_pattern=(
            values.get("INGESTION_REGION_PATTERN") or DEFAULT_REGION_PATTERN
        ),
        max_uncompressed_bytes=max_uncompressed,
        max_compression_ratio=max_ratio,
        max_archive_entries=max_entries,
        max_sheet_rows=max_rows,
    )
