"""Submit and query user feedback / article reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from django.contrib.auth import get_user_model

from news.feedback_constants import (
    FEEDBACK_CATEGORIES,
    FEEDBACK_TYPES,
    LEGACY_REASON_MAP,
)
from news.mongo_db import user_feedback_collection
from news.services import article_query
from notifications.delivery import notify_builtin_admins

User = get_user_model()

RATE_LIMIT_PER_HOUR = 10
DEDUPE_HOURS = 24


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_category(raw: str) -> str:
    key = str(raw or "").strip().lower()
    if key in FEEDBACK_CATEGORIES:
        return key
    if key in LEGACY_REASON_MAP:
        return LEGACY_REASON_MAP[key]
    return ""


def _normalize_type(raw: str, *, has_article: bool) -> str:
    key = str(raw or "").strip().lower()
    if key in FEEDBACK_TYPES:
        return key
    if has_article:
        return "article_report"
    return "app_feedback"


def _category_label(category: str) -> str:
    return FEEDBACK_CATEGORIES.get(category, category.replace("_", " ").title())


def _check_rate_limit(user_id: int) -> bool:
    since = _utc_now() - timedelta(hours=1)
    count = user_feedback_collection().count_documents(
        {"user_id": user_id, "created_at": {"$gte": since}}
    )
    return count < RATE_LIMIT_PER_HOUR


def _notify_admins_for_feedback(
    *,
    feedback_id: str,
    fb_type: str,
    category: str,
    message: str,
    user_id: int,
    article_id: Optional[str],
    post_title: str = "",
) -> None:
    label = _category_label(category)
    ntype = "admin_user_report" if fb_type == "article_report" else "admin_user_feedback"
    text = f"New user {fb_type.replace('_', ' ')}: {label}"
    if post_title:
        text = f"{text} — {post_title[:80]}"
    details = (message or label)[:500]
    notify_builtin_admins(
        ntype=ntype,
        text=text,
        details=details,
        important=True,
        meta={
            "feedback_id": feedback_id,
            "article_id": article_id or "",
            "category": category,
            "user_id": user_id,
            "post_title": post_title[:200] if post_title else "",
            "feedback_type": fb_type,
        },
        dedupe_key=f"feedback:{feedback_id}",
    )


def submit_user_feedback(
    user,
    *,
    fb_type: str = "",
    article_id: str = "",
    url: str = "",
    category: str = "",
    message: str = "",
    reason: str = "",
) -> tuple[dict, Optional[str], int]:
    """
    Persist feedback and notify built-in admins.
    Returns (serialized_doc, error_message, http_status).
    """
    user_id = user.pk
    if not _check_rate_limit(user_id):
        return {}, "Too many submissions. Please try again later.", 429

    article_id = str(article_id or "").strip()
    url = str(url or "").strip()
    message = str(message or reason or "").strip()[:2000]
    category = _normalize_category(category or reason)
    if not category:
        category = "other" if message else "misleading"

    fb_type = _normalize_type(fb_type, has_article=bool(article_id))
    if fb_type in {"article_report", "article_feedback"} and not article_id and not url:
        return {}, "article_id or url is required for article feedback.", 400
    if category == "other" and not message:
        return {}, "Please describe your feedback when selecting Other.", 400
    if category not in FEEDBACK_CATEGORIES:
        return {}, "Invalid feedback category.", 400

    post_title = ""
    if article_id:
        doc = article_query.get_article_by_id(article_id, user)
        if doc:
            post_title = str(doc.get("title") or "")
            if not url:
                url = str(doc.get("canonical_url") or doc.get("url") or "")

    now = _utc_now()
    dedupe_key = None
    if article_id and fb_type == "article_report":
        dedupe_key = f"{user_id}:{article_id}:{category}"

    col = user_feedback_collection()
    base_doc = {
        "user_id": user_id,
        "type": fb_type,
        "article_id": article_id or None,
        "url": url or None,
        "category": category,
        "message": message,
        "status": "pending",
        "admin_notes": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "dedupe_key": dedupe_key,
        "updated_at": now,
    }

    existing = None
    if dedupe_key:
        since = now - timedelta(hours=DEDUPE_HOURS)
        existing = col.find_one(
            {
                "user_id": user_id,
                "article_id": article_id,
                "category": category,
                "created_at": {"$gte": since},
            }
        )

    if existing:
        fid = existing["_id"]
        col.update_one(
            {"_id": fid},
            {"$set": {**base_doc, "created_at": existing.get("created_at", now)}},
        )
        doc = col.find_one({"_id": fid}) or existing
    else:
        base_doc["created_at"] = now
        inserted = col.insert_one(base_doc)
        doc = col.find_one({"_id": inserted.inserted_id}) or base_doc
        doc["_id"] = inserted.inserted_id

    feedback_id = str(doc["_id"])
    _notify_admins_for_feedback(
        feedback_id=feedback_id,
        fb_type=fb_type,
        category=category,
        message=message,
        user_id=user_id,
        article_id=article_id or None,
        post_title=post_title,
    )

    return serialize_feedback(doc), None, 201


def serialize_feedback(doc: dict, *, reporter_email: str = "") -> dict:
    created = doc.get("created_at")
    reviewed = doc.get("reviewed_at")
    return {
        "id": str(doc.get("_id")),
        "user_id": doc.get("user_id"),
        "reporter_email": reporter_email,
        "type": doc.get("type"),
        "article_id": doc.get("article_id"),
        "url": doc.get("url"),
        "category": doc.get("category"),
        "category_label": _category_label(str(doc.get("category") or "")),
        "message": doc.get("message") or "",
        "status": doc.get("status") or "pending",
        "admin_notes": doc.get("admin_notes"),
        "reviewed_by": doc.get("reviewed_by"),
        "reviewed_at": reviewed.isoformat() if hasattr(reviewed, "isoformat") else reviewed,
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }


def get_feedback_stats() -> dict:
    col = user_feedback_collection()
    status_pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    counts = {r["_id"]: r["count"] for r in col.aggregate(status_pipeline)}

    category_rows = list(
        col.aggregate(
            [
                {"$match": {"category": {"$exists": True, "$ne": ""}}},
                {"$group": {"_id": "$category", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 12},
            ]
        )
    )
    type_rows = list(
        col.aggregate(
            [
                {"$match": {"type": {"$exists": True, "$ne": ""}}},
                {"$group": {"_id": "$type", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        )
    )

    since = _utc_now() - timedelta(days=13)
    daily_rows = list(
        col.aggregate(
            [
                {"$match": {"created_at": {"$gte": since}}},
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}
                        },
                        "count": {"$sum": 1},
                    }
                },
                {"$sort": {"_id": 1}},
            ]
        )
    )
    daily = [{"date": r["_id"], "count": r["count"]} for r in daily_rows if r.get("_id")]

    return {
        "pending": counts.get("pending", 0),
        "reviewed": counts.get("reviewed", 0),
        "dismissed": counts.get("dismissed", 0),
        "total": sum(counts.values()),
        "by_status": {
            "pending": counts.get("pending", 0),
            "reviewed": counts.get("reviewed", 0),
            "dismissed": counts.get("dismissed", 0),
        },
        "by_category": {r["_id"]: r["count"] for r in category_rows if r.get("_id")},
        "by_type": {r["_id"]: r["count"] for r in type_rows if r.get("_id")},
        "daily": daily,
    }


def list_feedback(
    *,
    status: str = "",
    fb_type: str = "",
    category: str = "",
    article_id: str = "",
    limit: int = 50,
    skip: int = 0,
) -> list[dict]:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    if fb_type:
        query["type"] = fb_type
    if category:
        query["category"] = category
    if article_id:
        query["article_id"] = article_id

    cursor = (
        user_feedback_collection()
        .find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(min(limit, 200))
    )
    raw_rows = list(cursor)
    user_ids = {r.get("user_id") for r in raw_rows if r.get("user_id") is not None}
    emails = {}
    if user_ids:
        for u in User.objects.filter(pk__in=list(user_ids)).only("pk", "email"):
            emails[u.pk] = u.email

    return [
        serialize_feedback(r, reporter_email=emails.get(r.get("user_id"), ""))
        for r in raw_rows
    ]


def get_feedback_by_id(feedback_id: str) -> Optional[dict]:
    from bson import ObjectId

    try:
        oid = ObjectId(feedback_id)
    except Exception:
        return None
    doc = user_feedback_collection().find_one({"_id": oid})
    if not doc:
        return None
    reporter_email = ""
    uid = doc.get("user_id")
    if uid is not None:
        u = User.objects.filter(pk=uid).only("email").first()
        reporter_email = u.email if u else ""
    return serialize_feedback(doc, reporter_email=reporter_email)


def update_feedback(
    feedback_id: str,
    *,
    admin_user,
    status: str = "",
    admin_notes: Optional[str] = None,
    set_admin_notes: bool = False,
) -> Optional[dict]:
    from bson import ObjectId

    from news.feedback_constants import FEEDBACK_STATUSES

    try:
        oid = ObjectId(feedback_id)
    except Exception:
        return None

    updates: dict[str, Any] = {"updated_at": _utc_now()}
    if status:
        if status not in FEEDBACK_STATUSES:
            return None
        updates["status"] = status
        updates["reviewed_by"] = admin_user.pk
        updates["reviewed_at"] = _utc_now()
    if set_admin_notes:
        updates["admin_notes"] = str(admin_notes or "")[:2000]

    if len(updates) <= 1:
        return get_feedback_by_id(feedback_id)

    from pymongo import ReturnDocument

    res = user_feedback_collection().find_one_and_update(
        {"_id": oid},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if not res:
        return None
    reporter_email = ""
    uid = res.get("user_id")
    if uid is not None:
        u = User.objects.filter(pk=uid).only("email").first()
        reporter_email = u.email if u else ""
    return serialize_feedback(res, reporter_email=reporter_email)
