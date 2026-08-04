"""Column mapping between workbook headers and canonical fields.

A mapping profile is reusable: it records the dataset kind, the sheet, the
header row and the header→field assignments, and can be re-applied to a later
export with the same layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from app.ingestion.schemas import CanonicalSchema, DatasetKind, get_schema


class MappingError(Exception):
    """Raised when a mapping is not usable for the selected dataset."""


def normalize_header(value: Any) -> str:
    """Fold a header into a comparison key: lowercase alphanumeric words."""

    text = "" if value is None else " ".join(str(value).split())
    text = text.casefold().replace("%", " percent ").replace("#", " ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


@dataclass(frozen=True)
class ColumnMappingProfile:
    """A reusable header→canonical-field assignment."""

    name: str
    dataset_kind: DatasetKind
    sheet_name: str
    header_row: int = 1
    #: canonical field name -> workbook header text
    field_to_header: Mapping[str, str] = field(default_factory=dict)

    @property
    def schema(self) -> CanonicalSchema:
        return get_schema(self.dataset_kind)

    @property
    def mapped_fields(self) -> tuple[str, ...]:
        return tuple(sorted(self.field_to_header))

    def missing_required_fields(self) -> tuple[str, ...]:
        mapped = set(self.field_to_header)
        return tuple(
            name
            for name in self.schema.required_fields
            if name not in mapped or not str(self.field_to_header[name]).strip()
        )

    def validate(self) -> None:
        schema = self.schema
        known = set(schema.field_names)
        unknown = sorted(set(self.field_to_header) - known)
        if unknown:
            raise MappingError(
                f"Unknown canonical field(s) for {schema.title}: "
                + ", ".join(unknown)
            )
        duplicates = _duplicate_headers(self.field_to_header)
        if duplicates:
            raise MappingError(
                "The same column is mapped to more than one canonical field: "
                + ", ".join(duplicates)
            )
        missing = self.missing_required_fields()
        if missing:
            raise MappingError(
                f"{schema.title} mapping is missing required field(s): "
                + ", ".join(missing)
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "dataset_kind": self.dataset_kind.value,
            "sheet_name": self.sheet_name,
            "header_row": self.header_row,
            "field_to_header": dict(self.field_to_header),
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "ColumnMappingProfile":
        try:
            return cls(
                name=str(document["name"]),
                dataset_kind=DatasetKind(document["dataset_kind"]),
                sheet_name=str(document["sheet_name"]),
                header_row=int(document.get("header_row", 1)),
                field_to_header=dict(document.get("field_to_header") or {}),
            )
        except (KeyError, ValueError) as error:
            raise MappingError("Mapping profile document is malformed.") from error


def _duplicate_headers(field_to_header: Mapping[str, str]) -> list[str]:
    seen: dict[str, list[str]] = {}
    for canonical, header in field_to_header.items():
        seen.setdefault(normalize_header(header), []).append(canonical)
    return sorted(
        f"{header} -> {', '.join(sorted(fields))}"
        for header, fields in seen.items()
        if header and len(fields) > 1
    )


def suggest_mapping(
    headers: Sequence[str],
    dataset_kind: DatasetKind | str,
    *,
    extra_aliases: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Propose a header for each canonical field using configurable aliases.

    ``extra_aliases`` maps an additional header alias to a canonical field
    name, letting a deployment teach the pipeline site-specific SAP labels
    without a code change.
    """

    schema = get_schema(dataset_kind)
    alias_to_field: dict[str, str] = {}
    for canonical in schema.fields:
        alias_to_field.setdefault(normalize_header(canonical.name), canonical.name)
        for alias in canonical.aliases:
            alias_to_field.setdefault(normalize_header(alias), canonical.name)
    for alias, canonical_name in (extra_aliases or {}).items():
        if canonical_name in schema.field_names:
            alias_to_field[normalize_header(alias)] = canonical_name

    suggestion: dict[str, str] = {}
    for header in headers:
        key = normalize_header(header)
        if not key:
            continue
        canonical_name = alias_to_field.get(key)
        if canonical_name and canonical_name not in suggestion:
            suggestion[canonical_name] = header
    return suggestion


def build_profile(
    *,
    name: str,
    dataset_kind: DatasetKind | str,
    sheet_name: str,
    headers: Sequence[str],
    header_row: int = 1,
    overrides: Mapping[str, str] | None = None,
    extra_aliases: Mapping[str, str] | None = None,
) -> ColumnMappingProfile:
    """Suggest a mapping, apply user overrides, and validate the result."""

    proposed = suggest_mapping(
        headers,
        dataset_kind,
        extra_aliases=extra_aliases,
    )
    for canonical_name, header in (overrides or {}).items():
        if header is None or not str(header).strip():
            proposed.pop(canonical_name, None)
        else:
            proposed[canonical_name] = str(header)
    profile = ColumnMappingProfile(
        name=name,
        dataset_kind=DatasetKind(dataset_kind),
        sheet_name=sheet_name,
        header_row=header_row,
        field_to_header=proposed,
    )
    profile.validate()
    return profile
