"""Support for the archived (desensitised) SAP price-list export layout.

The archived export is a *wide* price list: one row per catalogue number with
the full cost breakdown (COGS, installation, warranty, freight, duty, tariff)
spread across columns, and no currency column at all — the whole workbook is
denominated in the SAP base currency.

That layout cannot be mapped by header aliases alone, so this module records
the layout once:

* the extra header aliases used by the archived export,
* the currency constant applied to every row,
* a helper that turns each populated sheet into a ready-to-validate
  :class:`~app.ingestion.pipeline.DatasetPlan`.

Nothing here bypasses the pipeline: the plans it produces still pass through
normalisation, row validation, quarantine, explicit user confirmation,
publication and activation.
"""

from __future__ import annotations

from typing import Mapping

from app.config import SAP_BASE_CURRENCY
from app.ingestion.mapping import ColumnMappingProfile, MappingError, build_profile
from app.ingestion.pipeline import DatasetPlan, ImportSession
from app.ingestion.schemas import DatasetKind

#: Headers seen in the archived export that the canonical aliases do not
#: already cover. Everything else is matched by the canonical field aliases.
ARCHIVED_HEADER_ALIASES: Mapping[str, str] = {
    "cat": "product_id",
    "cat no": "product_id",
    "description": "description",
    "prod family": "product_family",
    "prod type": "product_type",
    "list price": "list_price",
    "net price": "net_price",
    "minimum price": "minimum_price",
    "transfer price": "transfer_price",
    "cogs": "cogs",
    "installation cogs": "installation_cogs",
    "warranty cogs": "warranty_cogs",
    "cogs i&w": "cogs_installation_warranty",
    "cogs i w": "cogs_installation_warranty",
    "cogs mov avg": "cogs_moving_average",
    "freight": "freight",
    "duty": "duty",
    "tariff": "tariff",
    "service net price": "service_net_price",
    "service cogs": "service_cogs",
    "app cogs": "app_cogs",
}

#: The archived export carries no currency column; every row is denominated in
#: the SAP base currency.
ARCHIVED_CONSTANTS: Mapping[str, str] = {"currency": SAP_BASE_CURRENCY}

PROFILE_NAME_PREFIX = "sap-archived"


def archived_profile(
    session: ImportSession,
    sheet_name: str,
    *,
    header_row: int | None = None,
) -> ColumnMappingProfile:
    """Build the archived-layout pricing profile for one sheet."""

    return session.suggest_profile(
        sheet_name,
        DatasetKind.PRICING,
        header_row=header_row,
        extra_aliases=ARCHIVED_HEADER_ALIASES,
        constants=ARCHIVED_CONSTANTS,
        name=f"{PROFILE_NAME_PREFIX}:{sheet_name}",
    )


def archived_plans(
    session: ImportSession,
    *,
    header_row: int | None = None,
) -> tuple[tuple[DatasetPlan, ...], tuple[str, ...]]:
    """Plan every sheet of an archived export as a pricing dataset.

    Returns the usable plans and a human-readable note for each sheet that
    could not be mapped, so the operator sees exactly what was skipped rather
    than silently losing data.
    """

    plans: list[DatasetPlan] = []
    skipped: list[str] = []
    for sheet_name in session.sheet_names:
        try:
            profile = archived_profile(
                session, sheet_name, header_row=header_row
            )
        except MappingError as error:
            skipped.append(f"{sheet_name}: {error}")
            continue
        plans.append(DatasetPlan(DatasetKind.PRICING, profile))
    return tuple(plans), tuple(skipped)
