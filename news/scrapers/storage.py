"""Persist scraped article documents to MongoDB (separate from Django ORM / djongo models)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from django.conf import settings
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError


_client: Optional[MongoClient] = None


def _get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.MONGODB_URI)
    return _client


def raw_collection():
    db = _get_client()[settings.MONGODB_RAW_DATABASE]
    return db[settings.MONGODB_RAW_COLLECTION]


def ensure_indexes() -> None:
    col = raw_collection()
    col.create_index([("canonical_url", ASCENDING)], unique=True)
    col.create_index([("source_key", ASCENDING), ("fetched_at", ASCENDING)])
    col.create_index([("pipeline_status", ASCENDING)])
    col.create_index([("published_at", ASCENDING)])
    col.create_index([("title", ASCENDING)])


def insert_raw_if_new(doc: dict[str, Any]) -> bool:
    """
    Insert one article document if `canonical_url` is new.
    Expects structured fields (title, body_text, …); optional `raw_html` if enabled.
    Returns True if inserted, False if duplicate or error.
    """
    col = raw_collection()
    doc.setdefault("fetched_at", datetime.now(timezone.utc))
    doc.setdefault("pipeline_status", "pending")
    try:
        col.insert_one(doc)
        return True
    except DuplicateKeyError:
        return False


def exists_url(canonical_url: str) -> bool:
    return raw_collection().find_one({"canonical_url": canonical_url}, {"_id": 1}) is not None
