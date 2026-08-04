"""Shared helpers for ingestion tests."""

from __future__ import annotations

from typing import Mapping, Sequence

from app.ingestion.pipeline import (
    DatasetPlan,
    ImportPreview,
    ImportSession,
    run_import,
    start_import,
)
from app.ingestion.schemas import DatasetKind
from app.ingestion.storage import WorkbookStorage

#: Sheet name -> dataset kind for the synthetic fixtures.
FIXTURE_SHEET_PLAN: Mapping[str, DatasetKind] = {
    "Product Master": DatasetKind.PRODUCT_MASTER,
    "Pricing": DatasetKind.PRICING,
    "Quotations": DatasetKind.HISTORICAL_QUOTATION,
    "Compatibility": DatasetKind.COMPATIBILITY,
    "Cost Components": DatasetKind.COST_COMPONENT,
}


def build_plans(
    session: ImportSession,
    sheets: Sequence[str] | None = None,
) -> list[DatasetPlan]:
    selected = sheets if sheets is not None else [
        name for name in session.sheet_names if name in FIXTURE_SHEET_PLAN
    ]
    plans = []
    for sheet in selected:
        kind = FIXTURE_SHEET_PLAN[sheet]
        plans.append(DatasetPlan(kind, session.suggest_profile(sheet, kind)))
    return plans


def import_fixture(
    filename: str,
    payload: bytes,
    *,
    sheets: Sequence[str] | None = None,
    storage: WorkbookStorage | None = None,
) -> ImportPreview:
    session = start_import(filename, payload, storage=storage)
    return run_import(session, build_plans(session, sheets))


def issue_codes(preview: ImportPreview) -> set[str]:
    return {
        issue.code.value
        for dataset in preview.datasets
        for row in dataset.result.rejected_rows + dataset.result.warning_rows
        for issue in row.issues
    }
