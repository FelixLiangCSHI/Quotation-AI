"""Repository interfaces and their SQLAlchemy implementations."""

from __future__ import annotations

from app.repositories.interfaces import (
    ApprovalRepository,
    AuditEventRepository,
    DuplicateApprovalActionError,
    EmailRepository,
    QuotationNotFoundError,
    QuotationRepository,
    QuotationVersionConflictError,
    RepositoryError,
    UserRepository,
)
from app.repositories.sqlalchemy_repositories import (
    SqlAlchemyApprovalRepository,
    SqlAlchemyAuditEventRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyEmailRepository,
    SqlAlchemyQuotationRepository,
    SqlAlchemyUserRepository,
)

__all__ = [
    "ApprovalRepository",
    "AuditEventRepository",
    "DuplicateApprovalActionError",
    "EmailRepository",
    "QuotationNotFoundError",
    "QuotationRepository",
    "QuotationVersionConflictError",
    "RepositoryError",
    "SqlAlchemyApprovalRepository",
    "SqlAlchemyAuditEventRepository",
    "SqlAlchemyDocumentRepository",
    "SqlAlchemyEmailRepository",
    "SqlAlchemyQuotationRepository",
    "SqlAlchemyUserRepository",
    "UserRepository",
]
