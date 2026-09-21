"""Canonical JSON conversion for public platform value objects."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Mapping


def to_jsonable(value: object) -> object:
    """Convert platform dataclasses and value containers to JSON values."""

    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: to_jsonable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            (key.value if isinstance(key, Enum) else str(key)): to_jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [to_jsonable(item) for item in sorted(value, key=str)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")
