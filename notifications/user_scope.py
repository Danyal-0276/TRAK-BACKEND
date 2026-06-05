"""Per-user notification scope (account creation cutoff)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from django.contrib.auth import get_user_model

User = get_user_model()


def user_account_started_at(user) -> datetime | None:
    """UTC moment the account was created; None if unknown."""
    if user is None:
        return None
    joined = getattr(user, "date_joined", None)
    if not joined:
        return None
    if getattr(joined, "tzinfo", None) is None:
        from django.utils import timezone as dj_tz

        joined = dj_tz.make_aware(joined) if dj_tz.is_naive(joined) else joined.replace(tzinfo=timezone.utc)
    return joined.astimezone(timezone.utc)


def parse_mongo_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if getattr(value, "tzinfo", None) is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def effective_lookback_since(user, *, hours: int) -> datetime:
    """Lookback window capped at account creation — no pre-signup backfill."""
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    joined = user_account_started_at(user)
    if joined and joined > since:
        return joined
    return since


def user_id_variants(user) -> list:
    uid = user.pk
    return [uid, str(uid)]


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
