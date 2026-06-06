"""Per-user notification scope (account creation cutoff)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from django.contrib.auth import get_user_model

User = get_user_model()


def _as_utc_aware(value) -> datetime | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        from django.utils import timezone as dj_tz

        value = dj_tz.make_aware(value) if dj_tz.is_naive(value) else value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def user_account_started_at(user) -> datetime | None:
    """UTC moment the account was created; None if unknown."""
    if user is None:
        return None
    # TRAK User uses created_at (AbstractBaseUser), not Django's date_joined.
    started = getattr(user, "date_joined", None) or getattr(user, "created_at", None)
    return _as_utc_aware(started)


def user_interests_started_at(user) -> datetime | None:
    """When the user first saved feed keywords / interests."""
    if user is None or not getattr(user, "pk", None):
        return None
    from news.mongo_db import user_keywords_collection

    row = user_keywords_collection().find_one(
        {"user_id": user.pk},
        {"created_at": 1, "updated_at": 1},
    )
    if not row:
        return None
    ts = parse_mongo_datetime(row.get("created_at")) or parse_mongo_datetime(row.get("updated_at"))
    return ts


def parse_mongo_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def effective_lookback_since(user, *, hours: int) -> datetime:
    """Lookback window capped at account creation and keyword save — no pre-signup backfill."""
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    joined = user_account_started_at(user)
    if joined and joined > since:
        since = joined
    interests = user_interests_started_at(user)
    if interests and interests > since:
        since = interests
    return since


def user_id_variants(user) -> list:
    uid = user.pk
    return [uid, str(uid)]


def prune_stale_keyword_notifications(user, *, now: datetime | None = None) -> int:
    """
    Mark keyword alerts read when the article predates the user's account
    (fixes legacy backfill before created_at was honored).
    """
    from bson import ObjectId
    from news.mongo_db import notifications_collection, processed_collection

    joined = user_account_started_at(user)
    if not joined:
        return 0
    stamp = now or datetime.now(timezone.utc)
    ncol = notifications_collection()
    pcol = processed_collection()
    updated = 0
    cursor = ncol.find(
        {
            "user_id": {"$in": user_id_variants(user)},
            "audience": {"$ne": "admin"},
            "type": "keyword_match",
            "read": False,
        },
        {"meta": 1},
    ).limit(300)
    for doc in cursor:
        meta = doc.get("meta") or {}
        aid = str(meta.get("article_id") or "").strip()
        stale = False
        if aid:
            try:
                pdoc = pcol.find_one({"_id": ObjectId(aid)}, {"published_at": 1, "processed_at": 1})
            except Exception:
                pdoc = None
            if pdoc:
                pub_at = parse_mongo_datetime(pdoc.get("published_at"))
                proc_at = parse_mongo_datetime(pdoc.get("processed_at"))
                if pub_at and pub_at < joined:
                    stale = True
                elif proc_at and proc_at < joined:
                    stale = True
        if not stale:
            continue
        ncol.update_one(
            {"_id": doc["_id"]},
            {"$set": {"read": True, "updated_at": stamp, "suppressed_reason": "pre_account_article"}},
        )
        updated += 1
    return updated


def suppress_pre_account_notifications(user, *, now: datetime | None = None) -> int:
    """
    Mark unread notifications from before signup as read so badge counts match the feed.
    Returns number of documents updated.
    """
    from news.mongo_db import notifications_collection

    joined = user_account_started_at(user)
    if not joined:
        return 0
    stamp = now or datetime.now(timezone.utc)
    result = notifications_collection().update_many(
        {
            "user_id": {"$in": user_id_variants(user)},
            "audience": {"$ne": "admin"},
            "created_at": {"$lt": joined},
            "read": False,
        },
        {"$set": {"read": True, "updated_at": stamp, "suppressed_reason": "pre_account"}},
    )
    return int(result.modified_count)


def user_notifications_query(user, *, tab: str = "all") -> dict:
    query: dict = {"user_id": {"$in": user_id_variants(user)}, "audience": {"$ne": "admin"}}
    joined = user_account_started_at(user)
    if joined:
        query["created_at"] = {"$gte": joined}
    if tab == "keywords":
        query["type"] = "keyword_match"
    elif tab == "system":
        query["type"] = {"$in": ["system", "welcome_back"]}
    elif tab == "unread":
        query["read"] = False
    return query
