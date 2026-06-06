"""JSON-safe serialization for MongoDB document fields."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId


def _datetime_to_utc_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def mongo_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return _datetime_to_utc_z(value)
    if isinstance(value, dict):
        return {str(k): mongo_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mongo_json(v) for v in value]
    return value
