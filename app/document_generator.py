"""Backwards-compatible customer PDF entry point.

Phase 8 moved document generation into :mod:`app.documents`:

* :mod:`app.documents.context` builds the trusted, approved, customer-safe
  context;
* :mod:`app.documents.plan` validates and sanitises the Agent 4 DocumentPlan;
* :mod:`app.documents.renderer` renders the approved branded template through
  Jinja2 and a PDF engine, with a deterministic ReportLab fallback.

This module keeps the pre-Phase-8 function signature so existing call sites
continue to work, and simply delegates.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.config import DEMO_QUOTATION_VALIDITY_DAYS
from app.documents.context import (
    DocumentContextError,
    build_customer_document_context,
)
from app.documents.plan import DocumentPlan, deterministic_document_plan
from app.documents.renderer import (
    DocumentRenderError,
    PDF_MIME_TYPE,
    render_quotation_pdf,
    safe_document_filename,
)
from app.output_context import OutputGenerationError
from app.quotation_models import DocumentOutput, QuotationWorkflowState

__all__ = [
    "PDF_MIME_TYPE",
    "DocumentGenerationError",
    "generate_quotation_pdf",
    "safe_quotation_filename",
]


class DocumentGenerationError(OutputGenerationError):
    """Raised when an approved customer PDF cannot be produced."""


def safe_quotation_filename(quotation_id: str) -> str:
    """Legacy filename helper. Still path-traversal safe."""

    return safe_document_filename(quotation_id, quotation_version=1)


def generate_quotation_pdf(
    state: QuotationWorkflowState,
    *,
    logo_path: str | Path | None = None,
    as_of: date | None = None,
    validity_days: int = DEMO_QUOTATION_VALIDITY_DAYS,
    quotation_version: int = 1,
    plan: DocumentPlan | None = None,
    include_charts: bool = True,
) -> DocumentOutput:
    """Render the approved customer quotation PDF for ``state``.

    ``logo_path`` is accepted for backwards compatibility and is interpreted
    as an approved asset *name*; an arbitrary path is never read.
    """

    try:
        context = build_customer_document_context(
            state,
            quotation_version=quotation_version,
            as_of=as_of,
            validity_days=validity_days,
        )
    except DocumentContextError as error:
        raise DocumentGenerationError(str(error)) from error

    logo_asset = None if logo_path is None else Path(str(logo_path)).name
    try:
        rendered = render_quotation_pdf(
            context,
            plan or deterministic_document_plan(),
            include_charts=include_charts,
            logo_asset=logo_asset,
        )
    except DocumentRenderError as error:
        raise DocumentGenerationError(
            "The quotation PDF could not be generated."
        ) from error
    return DocumentOutput(
        filename=rendered.filename,
        mime_type=rendered.mime_type,
        bytes_data=rendered.content,
    )
