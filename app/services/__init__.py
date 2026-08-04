"""Service layer: transaction boundaries and use cases."""

from __future__ import annotations

from app.services.quotation_service import (
    LoadedQuotation,
    QuotationService,
    QuotationServiceError,
)
from app.services.session_reference import (
    ACTIVE_QUOTATION_KEY,
    ACTIVE_QUOTATION_VERSION_KEY,
    ACTIVE_USER_KEY,
    SESSION_REFERENCE_KEYS,
    SessionReference,
    clear_active_quotation,
    read_session_reference,
    set_active_quotation,
    set_active_user,
)
from app.services.unit_of_work import UnitOfWork

__all__ = [
    "ACTIVE_QUOTATION_KEY",
    "ACTIVE_QUOTATION_VERSION_KEY",
    "ACTIVE_USER_KEY",
    "SESSION_REFERENCE_KEYS",
    "LoadedQuotation",
    "QuotationService",
    "QuotationServiceError",
    "SessionReference",
    "UnitOfWork",
    "clear_active_quotation",
    "read_session_reference",
    "set_active_quotation",
    "set_active_user",
]
