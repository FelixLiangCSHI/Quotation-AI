"""Bridge between published pricing datasets and the pricing engine.

The pricing engine consumes :class:`app.pricing_data.PricingRecord`. This
module converts the canonical rows of a *published* pricing data version into
that shape, and preserves the synthetic dataset as the development fallback.

Nothing here changes the active version. If no version is active the caller
transparently falls back to synthetic data, which is a read-only default, not
a silent switch of a published dataset.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from app.config import SAP_BASE_CURRENCY
from app.ingestion.repository import (
    PricingDataRecordDTO,
    PricingDataRepository,
)
from app.ingestion.schemas import DatasetKind
from app.pricing_data import PricingRecord, load_pricing_records

#: Canonical pricing fields copied onto :class:`app.pricing_data.PricingRecord`.
#: The archived SAP export carries the full cost breakdown, which the pricing
#: engine needs to build a complete cost basis instead of COGS alone.
MONEY_FIELDS = (
    "list_price",
    "net_price",
    "minimum_price",
    "transfer_price",
    "cogs",
    "installation_cogs",
    "warranty_cogs",
    "cogs_installation_warranty",
    "cogs_moving_average",
    "freight",
    "duty",
    "tariff",
    "service_net_price",
    "service_cogs",
    "app_cogs",
)


@dataclass(frozen=True)
class ResolvedPricingSource:
    """The dataset the pricing engine will use, and where it came from."""

    records: tuple[PricingRecord, ...]
    version_label: str
    version_id: int | None
    is_synthetic_fallback: bool


def records_from_version(
    rows: Iterable[PricingDataRecordDTO],
) -> tuple[PricingRecord, ...]:
    records: list[PricingRecord] = []
    for row in rows:
        values = row.values
        product_id = str(values.get("product_id") or "")
        if not product_id:
            continue
        records.append(
            PricingRecord(
                source_id=_stable_source_id(
                    row.source_sheet, row.source_row_number, product_id
                ),
                source_sheet=row.source_sheet,
                product_id=product_id,
                description=str(values.get("description") or ""),
                product_family=str(values.get("product_family") or ""),
                product_type=str(values.get("product_type") or ""),
                currency=str(values.get("currency") or SAP_BASE_CURRENCY),
                **{
                    name: _decimal(values.get(name)) for name in MONEY_FIELDS
                },
            )
        )
    return tuple(records)


def resolve_pricing_source(
    repository: PricingDataRepository | None = None,
    *,
    version_id: int | None = None,
) -> ResolvedPricingSource:
    """Resolve the dataset for the pricing engine.

    ``version_id`` selects an explicit published version. Otherwise the active
    version is used, and the synthetic dataset is the fallback when none is
    active.
    """

    resolved = repository or PricingDataRepository()
    if version_id is not None:
        summary = resolved.get_version(version_id)
        rows = resolved.records_for_version(version_id, DatasetKind.PRICING)
        return ResolvedPricingSource(
            records=records_from_version(rows),
            version_label=summary.label,
            version_id=summary.id,
            is_synthetic_fallback=False,
        )

    active = resolved.get_active_version()
    if active is None:
        return ResolvedPricingSource(
            records=load_pricing_records(data_mode="synthetic"),
            version_label="synthetic-development-fallback",
            version_id=None,
            is_synthetic_fallback=True,
        )
    rows = resolved.records_for_version(active.id, DatasetKind.PRICING)
    return ResolvedPricingSource(
        records=records_from_version(rows),
        version_label=active.label,
        version_id=active.id,
        is_synthetic_fallback=False,
    )


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _stable_source_id(sheet: str, row_number: int, product_id: str) -> str:
    key = f"{sheet}|{row_number}|{product_id}".encode("utf-8")
    return "IMP-" + hashlib.sha256(key).hexdigest()[:16].upper()
