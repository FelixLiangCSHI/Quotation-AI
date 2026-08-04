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

#: Region codes are ISO-3166 alpha-2/alpha-3 style or an approved market code.
DEFAULT_REGION_PATTERN = r"^[A-Z]{2,3}(-[A-Z0-9]{1,3})?$"


@dataclass(frozen=True)
class IngestionConfig:
    supported_currencies: tuple[str, ...] = DEFAULT_SUPPORTED_CURRENCIES
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    storage_root: Path = DEFAULT_STORAGE_ROOT
    region_pattern: str = DEFAULT_REGION_PATTERN
    unit_aliases: Mapping[str, str] = DEFAULT_UNIT_ALIASES

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

    raw_root = (values.get("INGESTION_STORAGE_ROOT") or "").strip()
    storage_root = Path(raw_root) if raw_root else DEFAULT_STORAGE_ROOT

    return IngestionConfig(
        supported_currencies=currencies,
        max_upload_bytes=max_bytes,
        storage_root=storage_root,
        region_pattern=(
            values.get("INGESTION_REGION_PATTERN") or DEFAULT_REGION_PATTERN
        ),
    )
