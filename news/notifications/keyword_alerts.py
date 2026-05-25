"""Notify users when a newly processed article matches their saved keywords."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from news.mongo_db import notifications_collection, user_keywords_collection, user_preferences_collection
from news.services.article_query import _doc_haystack, _keyword_matches_hay
from notifications.realtime import fanout_notification

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _user_wants_keyword_push(user_id: Any) -> bool:
    row = user_preferences_collection().find_one({"user_id": user_id}) or {}
    push = row.get("push_enabled")
    alerts = row.get("keyword_alerts")
    if push is False or alerts is False:
        return False
    return True


def _already_notified(user_id: Any, canonical_url: str, keyword: str) -> bool:
    """Avoid duplicate pushes for the same article + keyword within 24h."""
    since = _utc_now() - timedelta(hours=24)
    return (
        notifications_collection().find_one(
            {
                "user_id": user_id,
                "type": "keyword_match",
                "keyword": keyword,
                "meta.canonical_url": canonical_url,
                "created_at": {"$gte": since},
            }
        )
        is not None
    )


def _matched_keywords(doc: dict, keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    hay = _doc_haystack(doc)
    out: list[str] = []
    for k in keywords:
        if _keyword_matches_hay(k, hay):
            out.append(k)
    return out


def notify_keyword_matches_for_article(processed_doc: dict) -> int:
    """
    After pipeline upserts processed_articles, fan out in-app + FCM alerts
    to users whose keywords match this article.
    Returns number of users notified.
    """
    canonical = str(processed_doc.get("canonical_url") or processed_doc.get("raw_canonical_url") or "").strip()
    if not canonical:
        return 0

    article_id = str(processed_doc.get("_id") or "")
    title = str(processed_doc.get("title") or "New article").strip() or "New article"
    summary = str(processed_doc.get("summary") or "").strip()

    sent = 0
    for row in user_keywords_collection().find({}):
        user_id = row.get("user_id")
        if user_id is None:
            continue
        keywords = [str(k).strip().lower() for k in (row.get("keywords") or []) if str(k).strip()]
        if not keywords:
            continue

        hits = _matched_keywords(processed_doc, keywords)
        if not hits:
            continue

        if not _user_wants_keyword_push(user_id):
            continue

        matched_kw = hits[0]
        if _already_notified(user_id, canonical, matched_kw):
            continue

        text = f"New article for “{matched_kw}”: {title[:120]}"
        now = _utc_now()
        payload = {
            "user_id": user_id,
            "type": "keyword_match",
            "text": text,
            "details": summary[:500],
            "keyword": matched_kw,
            "read": False,
            "important": False,
            "meta": {
                "article_id": article_id,
                "canonical_url": canonical,
                "matched_keyword": matched_kw,
                "post_title": title[:200],
            },
            "created_at": now,
            "updated_at": now,
        }
        try:
            inserted = notifications_collection().insert_one(payload)
            fanout_notification(
                user_id,
                {
                    "id": str(inserted.inserted_id),
                    "type": "Keyword match",
                    "text": text,
                    "details": payload["details"],
                    "keyword": matched_kw,
                    "important": False,
                    "read": False,
                    "created_at": now.isoformat(),
                    "meta": payload["meta"],
                },
            )
            sent += 1
        except Exception as exc:
            logger.warning("keyword alert failed user_id=%s: %s", user_id, exc)

    if sent:
        logger.info("keyword alerts sent=%s article=%s", sent, canonical[:80])
    return sent
