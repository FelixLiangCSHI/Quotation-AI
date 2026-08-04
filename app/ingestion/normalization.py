"""Normalisation of raw cell values into canonical typed values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ingestion.config import IngestionConfig, load_ingestion_config
from app.ingestion.schemas import FieldKind

_BLANK_TOKENS = frozenset({"", "-", "--", "n/a", "na", "null", "none", "#n/a"})
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%Y%m%d",
)


class NormalizationError(ValueError):
    """Raised when a raw value cannot be normalised to its canonical kind."""


@dataclass(frozen=True)
class NormalizedValue:
    value: Any
    warnings: tuple[str, ...] = ()


def normalize_value(
    raw: Any,
    kind: FieldKind,
    *,
    config: IngestionConfig | None = None,
) -> NormalizedValue:
    resolved = config or load_ingestion_config()
    if _is_blank(raw):
        return NormalizedValue(None)

    if kind is FieldKind.TEXT:
        return NormalizedValue(_text(raw))
    if kind is FieldKind.IDENTIFIER:
        return NormalizedValue(_identifier(raw))
    if kind is FieldKind.DECIMAL:
        return NormalizedValue(_decimal(raw))
    if kind is FieldKind.INTEGER:
        return NormalizedValue(_integer(raw))
    if kind is FieldKind.DATE:
        return NormalizedValue(_date(raw))
    if kind is FieldKind.CURRENCY:
        return NormalizedValue(_text(raw).upper())
    if kind is FieldKind.REGION:
        return NormalizedValue(_text(raw).upper().replace(" ", ""))
    if kind is FieldKind.UNIT:
        text = _text(raw)
        canonical = resolved.unit_aliases.get(text.casefold(), text.upper())
        warnings = (
            (f"Unit {text!r} was normalised to {canonical!r}.",)
            if canonical != text.upper()
            else ()
        )
        return NormalizedValue(canonical, warnings)
    raise NormalizationError(f"Unsupported field kind: {kind}")


def _is_blank(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, str):
        return raw.strip().casefold() in _BLANK_TOKENS
    return False


def _text(raw: Any) -> str:
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    if isinstance(raw, (datetime, date)):
        return raw.isoformat()
    return " ".join(str(raw).split())


def _identifier(raw: Any) -> str:
    text = _text(raw)
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    return text


def _decimal(raw: Any) -> Decimal:
    if isinstance(raw, bool):
        raise NormalizationError(f"{raw!r} is not a numeric value.")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return Decimal(str(raw))
    text = _text(raw)
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()").replace(",", "").replace("$", "").replace(" ", "")
    percentage = cleaned.endswith("%")
    if percentage:
        cleaned = cleaned[:-1]
    for symbol in ("€", "£", "¥"):
        cleaned = cleaned.replace(symbol, "")
    try:
        result = Decimal(cleaned)
    except (InvalidOperation, ValueError) as error:
        raise NormalizationError(f"{text!r} is not a valid number.") from error
    if not result.is_finite():
        raise NormalizationError(f"{text!r} is not a finite number.")
    return -result if negative else result


def _integer(raw: Any) -> int:
    value = _decimal(raw)
    if value != value.to_integral_value():
        raise NormalizationError(f"{raw!r} is not a whole number.")
    return int(value)


def _date(raw: Any) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        # Excel serial date (1900 date system).
        try:
            from openpyxl.utils.datetime import from_excel
        except ImportError as error:  # pragma: no cover
            raise NormalizationError("Excel serial dates require openpyxl.") from error
        converted = from_excel(raw)
        if isinstance(converted, datetime):
            return converted.date()
        if isinstance(converted, date):
            return converted
        raise NormalizationError(f"{raw!r} is not a valid date.")
    text = _text(raw)
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date()
    except ValueError as error:
        raise NormalizationError(f"{text!r} is not a valid date.") from error
