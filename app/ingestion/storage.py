"""Secure storage for uploaded workbooks.

Requirement: the raw workbook must never be committed to a public repository.
The pipeline therefore writes it through an interface whose default
implementation stores the bytes under a configurable path outside the
repository tree. An object-storage adapter can be substituted without touching
the pipeline.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.ingestion.config import IngestionConfig, load_ingestion_config


def file_hash(payload: bytes) -> str:
    """Stable content hash used for idempotent imports."""

    return hashlib.sha256(payload).hexdigest()


class WorkbookStorageError(RuntimeError):
    """Raised when a workbook cannot be stored or retrieved."""


@runtime_checkable
class WorkbookStorage(Protocol):
    """Object-storage-shaped interface for raw uploaded workbooks."""

    def store(self, *, content_hash: str, filename: str, payload: bytes) -> str:
        """Persist the payload and return an opaque storage URI."""

    def retrieve(self, uri: str) -> bytes:
        """Read back a previously stored payload."""

    def exists(self, uri: str) -> bool:
        ...


class LocalWorkbookStorage:
    """Filesystem-backed storage under a configurable secure root."""

    scheme = "file"

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        config: IngestionConfig | None = None,
    ) -> None:
        resolved = config or load_ingestion_config()
        self._root = Path(root) if root is not None else resolved.storage_root

    @property
    def root(self) -> Path:
        return self._root

    def store(self, *, content_hash: str, filename: str, payload: bytes) -> str:
        safe_name = Path(filename).name or "upload.xlsx"
        target_directory = self._root / content_hash[:2] / content_hash
        try:
            target_directory.mkdir(parents=True, exist_ok=True)
            target = target_directory / safe_name
            target.write_bytes(payload)
        except OSError as error:
            raise WorkbookStorageError(
                "The uploaded workbook could not be written to secure storage."
            ) from error
        return f"{self.scheme}://{target.as_posix()}"

    def retrieve(self, uri: str) -> bytes:
        path = self._path_for(uri)
        try:
            return path.read_bytes()
        except OSError as error:
            raise WorkbookStorageError(
                "The stored workbook could not be read."
            ) from error

    def exists(self, uri: str) -> bool:
        try:
            return self._path_for(uri).exists()
        except WorkbookStorageError:
            return False

    def _path_for(self, uri: str) -> Path:
        prefix = f"{self.scheme}://"
        if not uri.startswith(prefix):
            raise WorkbookStorageError(f"Unsupported storage URI: {uri!r}")
        return Path(uri[len(prefix) :])


class InMemoryWorkbookStorage:
    """Test double that keeps payloads in process memory."""

    scheme = "memory"

    def __init__(self) -> None:
        self._items: dict[str, bytes] = {}

    def store(self, *, content_hash: str, filename: str, payload: bytes) -> str:
        uri = f"{self.scheme}://{content_hash}/{Path(filename).name}"
        self._items[uri] = payload
        return uri

    def retrieve(self, uri: str) -> bytes:
        try:
            return self._items[uri]
        except KeyError as error:
            raise WorkbookStorageError(f"Unknown storage URI: {uri!r}") from error

    def exists(self, uri: str) -> bool:
        return uri in self._items
