"""Phase 8 customer document subsystem.

The subsystem is deliberately layered:

``context``
    Trusted, approved quotation values. Nothing else may reach a document.
``plan``
    The strict, sanitised :class:`DocumentPlan` an AI agent may propose.
``renderer``
    Template-based rendering (Jinja2 HTML, then a PDF engine) with a
    deterministic ReportLab fallback.
``service``
    Approval gating, role checks, persistence, invalidation and audit.
"""

from __future__ import annotations

from app.documents.context import (
    CustomerDocumentContext,
    CustomerLineItem,
    build_customer_document_context,
)
from app.documents.plan import (
    DOCUMENT_PLAN_VERSION,
    DEFAULT_SECTION_IDS,
    DocumentPlan,
    DocumentPlanError,
    DocumentSection,
    build_document_plan,
    deterministic_document_plan,
    sanitize_plan_text,
)
from app.documents.renderer import (
    DocumentRenderError,
    RenderedDocument,
    available_pdf_engines,
    render_quotation_pdf,
)

__all__ = [
    "DEFAULT_SECTION_IDS",
    "DOCUMENT_PLAN_VERSION",
    "CustomerDocumentContext",
    "CustomerLineItem",
    "DocumentPlan",
    "DocumentPlanError",
    "DocumentRenderError",
    "DocumentSection",
    "RenderedDocument",
    "available_pdf_engines",
    "build_customer_document_context",
    "build_document_plan",
    "deterministic_document_plan",
    "render_quotation_pdf",
    "sanitize_plan_text",
]
