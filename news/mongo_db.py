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


def chatbot_history_collection() -> Collection:
    name = getattr(settings, "MONGODB_CHATBOT_HISTORY_COLLECTION", "chatbot_history")
    return get_db()[name]


def chatbot_conversations_collection() -> Collection:
    name = getattr(settings, "MONGODB_CHATBOT_CONVERSATIONS_COLLECTION", "chatbot_conversations")
    return get_db()[name]


def notifications_collection() -> Collection:
    name = getattr(settings, "MONGODB_NOTIFICATIONS_COLLECTION", "notifications")
    return get_db()[name]


def device_tokens_collection() -> Collection:
    name = getattr(settings, "MONGODB_DEVICE_TOKENS_COLLECTION", "device_tokens")
    return get_db()[name]


def user_preferences_collection() -> Collection:
    name = getattr(settings, "MONGODB_USER_PREFERENCES_COLLECTION", "user_preferences")
    return get_db()[name]


def bookmarks_collection() -> Collection:
    name = getattr(settings, "MONGODB_BOOKMARKS_COLLECTION", "bookmarks")
    return get_db()[name]


def reactions_collection() -> Collection:
    name = getattr(settings, "MONGODB_REACTIONS_COLLECTION", "reactions")
    return get_db()[name]


def article_reports_collection() -> Collection:
    name = getattr(settings, "MONGODB_ARTICLE_REPORTS_COLLECTION", "article_reports")
    return get_db()[name]


def user_feedback_collection() -> Collection:
    name = getattr(settings, "MONGODB_USER_FEEDBACK_COLLECTION", "user_feedback")
    return get_db()[name]


def ensure_feedback_indexes() -> None:
    """Idempotent indexes for user feedback."""
    from pymongo import DESCENDING

    fb = user_feedback_collection()
    fb.create_index([("status", ASCENDING), ("created_at", DESCENDING)])
    fb.create_index([("article_id", ASCENDING), ("created_at", DESCENDING)])
    fb.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    try:
        fb.create_index(
            [("user_id", ASCENDING), ("article_id", ASCENDING), ("category", ASCENDING)],
            unique=True,
            sparse=True,
            name="user_feedback_article_category_unique",
        )
    except Exception:
        pass


def ensure_all_article_indexes() -> None:
    """Idempotent indexes for raw, processed, and user_keywords."""
    from news.scrapers import storage as raw_storage

    raw_storage.ensure_indexes()

    from pymongo import DESCENDING, TEXT

    proc = processed_collection()
    proc.create_index([("canonical_url", ASCENDING)], unique=True, sparse=True)
    proc.create_index([("raw_canonical_url", ASCENDING)])
    proc.create_index([("processed_at", ASCENDING)])
    proc.create_index([("processed_at", DESCENDING), ("_id", DESCENDING)])
    proc.create_index([("credibility_label", ASCENDING)])
    proc.create_index([("topic_keywords", ASCENDING)])
    proc.create_index([("primary_category", ASCENDING)])
    proc.create_index([("categories", ASCENDING)])
    proc.create_index([("primary_category", ASCENDING), ("processed_at", DESCENDING)])
    try:
        proc.create_index(
            [
                ("credibility_label", ASCENDING),
                ("processed_at", DESCENDING),
                ("_id", DESCENDING),
            ],
            name="feed_real_processed_at",
        )
    except Exception:
        pass
    try:
        proc.create_index(
            [("title", TEXT), ("summary", TEXT), ("clean_text", TEXT)],
            name="processed_articles_text",
        )
    except Exception:
        pass

    uk = user_keywords_collection()
    uk.create_index([("user_id", ASCENDING)], unique=True)

    ch = chatbot_history_collection()
    ch.create_index([("user_id", ASCENDING)], unique=True)

    cc = chatbot_conversations_collection()
    cc.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])

    notif = notifications_collection()
    notif.create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])
    notif.create_index([("user_id", ASCENDING), ("read", ASCENDING)])

    tokens = device_tokens_collection()
    tokens.create_index([("user_id", ASCENDING), ("token", ASCENDING)], unique=True)

    prefs = user_preferences_collection()
    prefs.create_index([("user_id", ASCENDING)], unique=True)

    bookmarks = bookmarks_collection()
    bookmarks.create_index([("user_id", ASCENDING), ("article_id", ASCENDING)], unique=True)

    reactions = reactions_collection()
    reactions.create_index([("user_id", ASCENDING), ("article_id", ASCENDING)], unique=True)
    try:
        reactions.create_index(
            [("article_id", ASCENDING), ("reaction", ASCENDING)],
            name="reactions_article_reaction",
        )
    except Exception:
        pass

    ensure_feedback_indexes()
