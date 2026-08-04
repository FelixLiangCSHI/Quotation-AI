"""Repository interfaces and their SQLAlchemy implementations."""

from __future__ import annotations

from app.repositories.interfaces import (
    ApprovalRepository,
    AuditEventRepository,
    DuplicateApprovalActionError,
    QuotationNotFoundError,
    QuotationRepository,
    QuotationVersionConflictError,
    RepositoryError,
    UserRepository,
)
from app.repositories.sqlalchemy_repositories import (
    SqlAlchemyApprovalRepository,
    SqlAlchemyAuditEventRepository,
    SqlAlchemyQuotationRepository,
    SqlAlchemyUserRepository,
)

__all__ = [
    "ApprovalRepository",
    "AuditEventRepository",
    "DuplicateApprovalActionError",
    "QuotationNotFoundError",
    "QuotationRepository",
    "QuotationVersionConflictError",
    "RepositoryError",
    "SqlAlchemyApprovalRepository",
    "SqlAlchemyAuditEventRepository",
    "SqlAlchemyQuotationRepository",
    "SqlAlchemyUserRepository",
    "UserRepository",
]
