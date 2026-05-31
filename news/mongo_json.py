"""JSON-safe serialization for MongoDB document fields."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId


def mongo_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): mongo_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mongo_json(v) for v in value]
    return value
