"""Create in-app notifications and fan out push, email, and websocket."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from django.conf import settings
from django.contrib.auth import get_user_model

from news.mongo_json import mongo_json
from news.mongo_db import notifications_collection, user_preferences_collection
from notifications.email_delivery import send_notification_email
from notifications.realtime import fanout_notification

logger = logging.getLogger(__name__)
User = get_user_model()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _user_by_id(user_id: Any):
    try:
        return User.objects.filter(pk=int(user_id)).first()
    except (TypeError, ValueError):
        return User.objects.filter(pk=user_id).first()


def _channels_for_user(user_id: Any, *, audience: str, ntype: str) -> dict[str, bool]:
    row = user_preferences_collection().find_one({"user_id": user_id}) or {}
    if audience == "admin" or str(ntype).startswith("admin_"):
        return {"push": True, "email": True, "in_app": True}
    if row.get("notifications_enabled") is False:
        return {"push": False, "email": False, "in_app": False}
    push = row.get("push_enabled")
    email = row.get("email_enabled")
    if ntype == "keyword_match":
        if row.get("keyword_alerts") is False:
            return {"push": False, "email": False, "in_app": True}
        # Keyword alerts: in-app (WebSocket) + FCM on mobile only — never email.
        return {
            "push": push is not False,
            "email": False,
            "in_app": True,
        }
    return {
        "push": push is not False,
        "email": email is not False,
        "in_app": True,
    }


def create_notification(
    user_id: Any,
    *,
    ntype: str = "system",
    text: str,
    details: str = "",
    keyword: Optional[str] = None,
    important: bool = False,
    meta: Optional[dict] = None,
    audience: str = "user",
    title: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    dedupe_hours: int = 24,
) -> Optional[str]:
    """
    Persist notification and deliver on enabled channels.
    Returns notification id string or None if deduped.
    """
    if not user_id or not str(text or "").strip():
        return None

    if dedupe_key:
        since = _utc_now()
        from datetime import timedelta

        since = since - timedelta(hours=dedupe_hours)
        existing = notifications_collection().find_one(
            {
                "user_id": user_id,
                "dedupe_key": dedupe_key,
                "created_at": {"$gte": since},
            }
        )
        if existing:
            return str(existing.get("_id"))

    now = _utc_now()
    safe_meta = mongo_json(meta or {})
    doc = {
        "user_id": user_id,
        "audience": audience,
        "type": ntype,
        "text": str(text).strip(),
        "details": str(details or "").strip(),
        "keyword": keyword,
        "important": bool(important),
        "read": False,
        "meta": safe_meta,
        "dedupe_key": dedupe_key,
        "created_at": now,
        "updated_at": now,
    }
    inserted = notifications_collection().insert_one(doc)
    nid = str(inserted.inserted_id)

    channels = _channels_for_user(user_id, audience=audience, ntype=ntype)
    payload = {
        "id": nid,
        "type": ntype,
        "title": title or _default_title(ntype),
        "text": doc["text"],
        "details": doc["details"],
        "keyword": keyword,
        "important": important,
        "read": False,
        "created_at": mongo_json(now),
        "meta": safe_meta,
        "audience": audience,
    }

    if channels["in_app"]:
        fanout_notification(user_id, payload, audience=audience, send_push=channels["push"])

    if channels["email"] and ntype != "keyword_match":
        user = _user_by_id(user_id)
        if user and user.email:
            try:
                send_notification_email(
                    user.email,
                    subject=payload["title"],
                    body=doc["text"],
                    details=doc["details"],
                )
            except Exception as exc:
                logger.warning("notification email failed user_id=%s: %s", user_id, exc)

    return nid


def _default_title(ntype: str) -> str:
    mapping = {
        "keyword_match": "New article for your keywords",
        "welcome_back": "Welcome back to TRAK",
        "system": "TRAK",
        "admin_pipeline_error": "Pipeline error",
        "admin_system": "TRAK Admin",
        "admin_user_report": "User report",
        "admin_user_feedback": "User feedback",
    }
    return mapping.get(ntype, "TRAK")


def notify_all_admins(
    *,
    ntype: str,
    text: str,
    details: str = "",
    important: bool = True,
    meta: Optional[dict] = None,
    dedupe_key: Optional[str] = None,
) -> int:
    """Send the same admin alert to every admin user."""
    count = 0
    for admin in User.objects.filter(role=User.Role.ADMIN, is_active=True):
        nid = create_notification(
            admin.pk,
            ntype=ntype,
            text=text,
            details=details,
            important=important,
            meta=meta,
            audience="admin",
            dedupe_key=f"{dedupe_key}:{admin.pk}" if dedupe_key else None,
        )
        if nid:
            count += 1
    return count


def notify_builtin_admins(
    *,
    ntype: str,
    text: str,
    details: str = "",
    important: bool = True,
    meta: Optional[dict] = None,
    dedupe_key: Optional[str] = None,
) -> int:
    """Send admin alert only to the three built-in admin accounts."""
    emails = [e.lower() for e in getattr(settings, "BUILTIN_ADMIN_EMAILS_LIST", []) or []]
    if not emails:
        return notify_all_admins(
            ntype=ntype,
            text=text,
            details=details,
            important=important,
            meta=meta,
            dedupe_key=dedupe_key,
        )
    count = 0
    for admin in User.objects.filter(
        email__in=emails, role=User.Role.ADMIN, is_active=True
    ):
        nid = create_notification(
            admin.pk,
            ntype=ntype,
            text=text,
            details=details,
            important=important,
            meta=meta,
            audience="admin",
            dedupe_key=f"{dedupe_key}:{admin.pk}" if dedupe_key else None,
        )
        if nid:
            count += 1
    return count
