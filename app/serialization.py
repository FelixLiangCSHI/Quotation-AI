from __future__ import annotations

import base64
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from app.config import CUSTOMER_PROHIBITED_FIELDS


def to_jsonable(value: Any) -> Any:
    return _to_jsonable(value, customer_facing=False)


def to_customer_jsonable(value: Any) -> Any:
    return _to_jsonable(value, customer_facing=True)


def _to_jsonable(value: Any, *, customer_facing: bool) -> Any:
    if is_dataclass(value):
        return {
            item.name: _to_jsonable(
                getattr(value, item.name),
                customer_facing=customer_facing,
            )
            for item in fields(value)
            if not customer_facing
            or (
                item.metadata.get("customer_visible", True)
                and item.name not in CUSTOMER_PROHIBITED_FIELDS
            )
        }
    if isinstance(value, dict):
        return {
            key: _to_jsonable(item, customer_facing=customer_facing)
            for key, item in value.items()
            if not customer_facing
            or str(key).casefold() not in CUSTOMER_PROHIBITED_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [
            _to_jsonable(item, customer_facing=customer_facing) for item in value
        ]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value