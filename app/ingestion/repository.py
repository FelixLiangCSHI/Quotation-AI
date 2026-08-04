"""Persistence for published pricing data versions.

The repository is the single place where a version is created, published,
activated or deactivated. Nothing here happens implicitly: publication and
activation are separate explicit calls, and the active version never changes
as a side effect of an import.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db import models
from app.db.session import get_session_factory
from app.ingestion.mapping import ColumnMappingProfile
from app.ingestion.pipeline import ImportPreview
from app.ingestion.schemas import DatasetKind
from app.quotation_models import utc_now

#: Label used for the built-in synthetic development dataset.
SYNTHETIC_VERSION_LABEL = "synthetic-development-fallback"

STATUS_STAGED = "staged"
STATUS_PUBLISHED = "published"


class PricingDataRepositoryError(RuntimeError):
    """Raised when a pricing data version operation cannot be completed."""


class DuplicateImportError(PricingDataRepositoryError):
    """Raised when a workbook with the same hash has already been imported."""

    def __init__(self, checksum: str, label: str) -> None:
        super().__init__(
            f"A pricing data version was already imported from this workbook "
            f"(hash {checksum[:12]}…, version {label!r})."
        )
        self.checksum = checksum
        self.label = label


class PricingDataVersionNotFoundError(PricingDataRepositoryError):
    """Raised when a version cannot be located."""


@dataclass(frozen=True)
class PricingDataVersionSummary:
    """A read model describing one pricing data version."""

    id: int
    label: str
    status: str
    is_active: bool
    source_filename: str
    checksum: str
    uploaded_by: str
    uploaded_at: datetime | None
    published_at: datetime | None
    activated_at: datetime | None
    row_count: int
    warning_row_count: int
    rejected_row_count: int
    storage_uri: str
    mapping_profile: Mapping[str, Any]
    validation_summary: Mapping[str, Any]
    notes: str = ""


@dataclass(frozen=True)
class PricingDataRecordDTO:
    dataset_kind: DatasetKind
    source_sheet: str
    source_row_number: int
    product_id: str
    has_warnings: bool
    values: Mapping[str, Any]


class PricingDataRepository:
    """Read/write access to published pricing datasets.

    The pricing engine reads through :meth:`records_for_active_version` or
    :meth:`records_for_version`; it never reads a staged version.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory()

    # -- writes -----------------------------------------------------------

    def create_version_from_preview(
        self,
        preview: ImportPreview,
        *,
        label: str,
        uploaded_by: str,
        notes: str = "",
        allow_duplicate_hash: bool = False,
    ) -> PricingDataVersionSummary:
        """Stage a version from a reviewed import. Nothing is activated."""

        counts = preview.counts
        with self._session_factory() as session:
            existing = self._version_by_checksum(
                session, preview.workbook.content_hash
            )
            if existing is not None and not allow_duplicate_hash:
                raise DuplicateImportError(
                    preview.workbook.content_hash, existing.label
                )
            if self._version_by_label(session, label) is not None:
                raise PricingDataRepositoryError(
                    f"A pricing data version named {label!r} already exists."
                )

            now = utc_now()
            version = models.PricingDataVersion(
                label=label,
                status=STATUS_STAGED,
                source_kind="excel_import",
                source_filename=preview.workbook.filename,
                checksum=preview.workbook.content_hash,
                storage_uri=preview.storage_uri,
                uploaded_by=uploaded_by,
                uploaded_at=now,
                row_count=counts["valid"] + counts["warning"],
                warning_row_count=counts["warning"],
                rejected_row_count=counts["rejected"],
                mapping_profile={
                    dataset.dataset_kind.value: dataset.profile.to_dict()
                    for dataset in preview.datasets
                },
                validation_summary=preview.summary(),
                notes=notes,
                is_active=False,
            )
            session.add(version)
            session.flush()

            for dataset in preview.datasets:
                for row in dataset.result.accepted_rows:
                    session.add(
                        models.PricingDataRecord(
                            version_id=version.id,
                            dataset_kind=dataset.dataset_kind.value,
                            source_sheet=dataset.sheet_name,
                            source_row_number=row.row_number,
                            product_id=str(row.values.get("product_id") or ""),
                            has_warnings=row.has_warnings,
                            payload=_jsonable_mapping(row.values),
                        )
                    )
                for row in dataset.result.rejected_rows:
                    session.add(
                        models.PricingDataRejection(
                            version_id=version.id,
                            dataset_kind=dataset.dataset_kind.value,
                            source_sheet=dataset.sheet_name,
                            source_row_number=row.row_number,
                            issues=[issue.to_dict() for issue in row.issues],
                            payload=_jsonable_mapping(row.values),
                        )
                    )

            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise PricingDataRepositoryError(
                    "The pricing data version could not be stored."
                ) from error
            return self._to_summary(version)

    def publish(self, version_id: int) -> PricingDataVersionSummary:
        """Mark a staged version as published. Does not activate it."""

        with self._session_factory() as session:
            version = self._require(session, version_id)
            if version.status == STATUS_PUBLISHED:
                return self._to_summary(version)
            version.status = STATUS_PUBLISHED
            version.published_at = utc_now()
            session.commit()
            return self._to_summary(version)

    def activate(self, version_id: int) -> PricingDataVersionSummary:
        """Make a published version the active pricing source.

        Explicit by design: importing or publishing never does this.
        """

        with self._session_factory() as session:
            version = self._require(session, version_id)
            if version.status != STATUS_PUBLISHED:
                raise PricingDataRepositoryError(
                    f"Version {version.label!r} must be published before it can "
                    "be activated."
                )
            for other in session.scalars(
                select(models.PricingDataVersion).where(
                    models.PricingDataVersion.is_active.is_(True)
                )
            ):
                other.is_active = False
            version.is_active = True
            version.activated_at = utc_now()
            session.commit()
            return self._to_summary(version)

    def deactivate_all(self) -> None:
        with self._session_factory() as session:
            for version in session.scalars(
                select(models.PricingDataVersion).where(
                    models.PricingDataVersion.is_active.is_(True)
                )
            ):
                version.is_active = False
            session.commit()

    def register_synthetic_fallback(
        self,
        *,
        label: str = SYNTHETIC_VERSION_LABEL,
        row_count: int = 0,
    ) -> PricingDataVersionSummary:
        """Preserve the synthetic dataset as a published development version."""

        with self._session_factory() as session:
            existing = self._version_by_label(session, label)
            if existing is not None:
                return self._to_summary(existing)
            now = utc_now()
            version = models.PricingDataVersion(
                label=label,
                status=STATUS_PUBLISHED,
                source_kind="synthetic",
                source_filename="Data/synthetic/pricing_demo.csv",
                checksum="",
                uploaded_by="system",
                uploaded_at=now,
                published_at=now,
                row_count=row_count,
                notes=(
                    "Synthetic development fallback dataset. Not real "
                    "commercial data."
                ),
                is_active=False,
            )
            session.add(version)
            session.commit()
            return self._to_summary(version)

    # -- reads ------------------------------------------------------------

    def list_versions(self) -> tuple[PricingDataVersionSummary, ...]:
        with self._session_factory() as session:
            versions = session.scalars(
                select(models.PricingDataVersion).order_by(
                    models.PricingDataVersion.id.desc()
                )
            ).all()
            return tuple(self._to_summary(version) for version in versions)

    def get_version(self, version_id: int) -> PricingDataVersionSummary:
        with self._session_factory() as session:
            return self._to_summary(self._require(session, version_id))

    def get_version_by_label(
        self, label: str
    ) -> PricingDataVersionSummary | None:
        with self._session_factory() as session:
            version = self._version_by_label(session, label)
            return None if version is None else self._to_summary(version)

    def find_version_by_checksum(
        self, checksum: str
    ) -> PricingDataVersionSummary | None:
        with self._session_factory() as session:
            version = self._version_by_checksum(session, checksum)
            return None if version is None else self._to_summary(version)

    def get_active_version(self) -> PricingDataVersionSummary | None:
        with self._session_factory() as session:
            version = session.scalars(
                select(models.PricingDataVersion).where(
                    models.PricingDataVersion.is_active.is_(True)
                )
            ).first()
            return None if version is None else self._to_summary(version)

    def records_for_version(
        self,
        version_id: int,
        dataset_kind: DatasetKind | str | None = None,
    ) -> tuple[PricingDataRecordDTO, ...]:
        with self._session_factory() as session:
            version = self._require(session, version_id)
            if version.status != STATUS_PUBLISHED:
                raise PricingDataRepositoryError(
                    f"Version {version.label!r} is not published; the pricing "
                    "engine may only read published datasets."
                )
            statement = select(models.PricingDataRecord).where(
                models.PricingDataRecord.version_id == version_id
            )
            if dataset_kind is not None:
                statement = statement.where(
                    models.PricingDataRecord.dataset_kind
                    == DatasetKind(dataset_kind).value
                )
            rows = session.scalars(
                statement.order_by(models.PricingDataRecord.id)
            ).all()
            return tuple(_to_record_dto(row) for row in rows)

    def records_for_active_version(
        self,
        dataset_kind: DatasetKind | str | None = None,
    ) -> tuple[PricingDataRecordDTO, ...]:
        active = self.get_active_version()
        if active is None:
            return ()
        return self.records_for_version(active.id, dataset_kind)

    def rejections_for_version(
        self, version_id: int
    ) -> tuple[Mapping[str, Any], ...]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(models.PricingDataRejection)
                .where(models.PricingDataRejection.version_id == version_id)
                .order_by(models.PricingDataRejection.id)
            ).all()
            return tuple(
                {
                    "dataset_kind": row.dataset_kind,
                    "sheet": row.source_sheet,
                    "row_number": row.source_row_number,
                    "issues": list(row.issues or []),
                    "values": dict(row.payload or {}),
                }
                for row in rows
            )

    # -- mapping profiles --------------------------------------------------

    def save_mapping_profile(
        self,
        profile: ColumnMappingProfile,
        *,
        created_by: str = "",
    ) -> None:
        with self._session_factory() as session:
            record = session.scalars(
                select(models.ColumnMappingProfileRecord).where(
                    models.ColumnMappingProfileRecord.name == profile.name
                )
            ).first()
            if record is None:
                record = models.ColumnMappingProfileRecord(name=profile.name)
                session.add(record)
            record.dataset_kind = profile.dataset_kind.value
            record.sheet_name = profile.sheet_name
            record.header_row = profile.header_row
            record.definition = profile.to_dict()
            record.created_by = created_by
            session.commit()

    def load_mapping_profile(self, name: str) -> ColumnMappingProfile | None:
        with self._session_factory() as session:
            record = session.scalars(
                select(models.ColumnMappingProfileRecord).where(
                    models.ColumnMappingProfileRecord.name == name
                )
            ).first()
            if record is None:
                return None
            return ColumnMappingProfile.from_dict(record.definition)

    def list_mapping_profiles(self) -> tuple[ColumnMappingProfile, ...]:
        with self._session_factory() as session:
            records = session.scalars(
                select(models.ColumnMappingProfileRecord).order_by(
                    models.ColumnMappingProfileRecord.name
                )
            ).all()
            return tuple(
                ColumnMappingProfile.from_dict(record.definition)
                for record in records
            )

    # -- helpers -----------------------------------------------------------

    def _require(
        self, session: Session, version_id: int
    ) -> models.PricingDataVersion:
        version = session.get(models.PricingDataVersion, version_id)
        if version is None:
            raise PricingDataVersionNotFoundError(
                f"Pricing data version {version_id} was not found."
            )
        return version

    @staticmethod
    def _version_by_label(
        session: Session, label: str
    ) -> models.PricingDataVersion | None:
        return session.scalars(
            select(models.PricingDataVersion).where(
                models.PricingDataVersion.label == label
            )
        ).first()

    @staticmethod
    def _version_by_checksum(
        session: Session, checksum: str
    ) -> models.PricingDataVersion | None:
        if not checksum:
            return None
        return session.scalars(
            select(models.PricingDataVersion).where(
                models.PricingDataVersion.checksum == checksum
            )
        ).first()

    @staticmethod
    def _to_summary(
        version: models.PricingDataVersion,
    ) -> PricingDataVersionSummary:
        return PricingDataVersionSummary(
            id=version.id,
            label=version.label,
            status=version.status,
            is_active=bool(version.is_active),
            source_filename=version.source_filename,
            checksum=version.checksum,
            uploaded_by=version.uploaded_by,
            uploaded_at=version.uploaded_at,
            published_at=version.published_at,
            activated_at=version.activated_at,
            row_count=version.row_count,
            warning_row_count=version.warning_row_count,
            rejected_row_count=version.rejected_row_count,
            storage_uri=version.storage_uri,
            mapping_profile=dict(version.mapping_profile or {}),
            validation_summary=dict(version.validation_summary or {}),
            notes=version.notes,
        )


def _to_record_dto(row: models.PricingDataRecord) -> PricingDataRecordDTO:
    return PricingDataRecordDTO(
        dataset_kind=DatasetKind(row.dataset_kind),
        source_sheet=row.source_sheet,
        source_row_number=row.source_row_number,
        product_id=row.product_id,
        has_warnings=bool(row.has_warnings),
        values=dict(row.payload or {}),
    )


def _jsonable_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    return {name: _jsonable(value) for name, value in values.items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def sequence_labels(summaries: Sequence[PricingDataVersionSummary]) -> tuple[str, ...]:
    return tuple(summary.label for summary in summaries)
