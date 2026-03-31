"""Shared MongoDB client and collection accessors (pymongo)."""

from __future__ import annotations

from typing import Optional

from django.conf import settings
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

_client: Optional[MongoClient] = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.MONGODB_URI)
    return _client


def get_db() -> Database:
    return get_client()[settings.MONGODB_RAW_DATABASE]


def raw_collection() -> Collection:
    return get_db()[settings.MONGODB_RAW_COLLECTION]


def processed_collection() -> Collection:
    name = getattr(settings, "MONGODB_PROCESSED_COLLECTION", "processed_articles")
    return get_db()[name]


def user_keywords_collection() -> Collection:
    name = getattr(settings, "MONGODB_USER_KEYWORDS_COLLECTION", "user_keywords")
    return get_db()[name]


def ensure_all_article_indexes() -> None:
    """Idempotent indexes for raw, processed, and user_keywords."""
    from news.scrapers import storage as raw_storage

    raw_storage.ensure_indexes()

    proc = processed_collection()
    proc.create_index([("canonical_url", ASCENDING)], unique=True, sparse=True)
    proc.create_index([("raw_canonical_url", ASCENDING)])
    proc.create_index([("processed_at", ASCENDING)])
    proc.create_index([("credibility_label", ASCENDING)])
    proc.create_index([("topic_keywords", ASCENDING)])

    uk = user_keywords_collection()
    uk.create_index([("user_id", ASCENDING)], unique=True)
