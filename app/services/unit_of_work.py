"""Service-layer transaction boundary.

A unit of work owns exactly one database session and one transaction. All
repositories inside it share that session, so a failure anywhere in a service
call rolls back every write the call made.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_session_factory
from app.repositories.sqlalchemy_repositories import (
    SqlAlchemyApprovalRepository,
    SqlAlchemyAuditEventRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyEmailRepository,
    SqlAlchemyQuotationRepository,
    SqlAlchemyUserRepository,
)


class UnitOfWork:
    """Context manager providing repositories bound to a single transaction.

    Commits on clean exit, rolls back on any exception. Nothing is written
    unless the whole block succeeds.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._session: Session | None = None
        self._committed = False

    def __enter__(self) -> "UnitOfWork":
        self._session = self._session_factory()
        self._committed = False
        self.users = SqlAlchemyUserRepository(self._session)
        self.quotations = SqlAlchemyQuotationRepository(self._session)
        self.audit_events = SqlAlchemyAuditEventRepository(self._session)
        self.approvals = SqlAlchemyApprovalRepository(self._session)
        self.emails = SqlAlchemyEmailRepository(self._session)
        self.documents = SqlAlchemyDocumentRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        assert self._session is not None
        try:
            if exc_type is not None:
                self._session.rollback()
            elif not self._committed:
                # A block that exits without an explicit commit is treated as
                # abandoned; nothing is persisted implicitly.
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
        return False

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active.")
        return self._session

    def commit(self) -> None:
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        self.session.rollback()
        self._committed = False
