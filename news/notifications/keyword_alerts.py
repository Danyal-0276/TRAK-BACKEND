"""Notify users when a newly processed article matches their saved keywords."""

from __future__ import annotations

import logging

from datetime import datetime, timedelta, timezone

from news.category_matching import interest_matches_hay, user_follows_all_categories
from news.moderation_rules import article_visible_to_users
from news.mongo_db import processed_collection, user_keywords_collection
from news.services.article_query import _doc_haystack
from notifications.delivery import create_notification

logger = logging.getLogger(__name__)


def _matched_keywords(doc: dict, keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    if user_follows_all_categories(keywords):
        return ["your topics"]
    hay = _doc_haystack(doc)
    return [k for k in keywords if interest_matches_hay(k, hay)]


def notify_keyword_matches_for_article(processed_doc: dict) -> int:
    if not article_visible_to_users(processed_doc):
        return 0

    canonical = str(processed_doc.get("canonical_url") or processed_doc.get("raw_canonical_url") or "").strip()
    if not canonical:
        return 0

    article_id = str(processed_doc.get("_id") or "")
    title = str(processed_doc.get("title") or "New article").strip() or "New article"
    summary = str(processed_doc.get("summary") or "").strip()
    source = str(processed_doc.get("source_key") or "").strip()

    sent = 0
    for row in user_keywords_collection().find({}):
        user_id = row.get("user_id")
        if user_id is None:
            continue
        keywords = [str(k).strip().lower() for k in (row.get("keywords") or []) if str(k).strip()]
        hits = _matched_keywords(processed_doc, keywords)
        if not hits:
            continue

        matched_kw = hits[0]
        if matched_kw == "your topics":
            text = f"New article in your interests: {title[:120]}"
        else:
            text = f"New article for “{matched_kw}”: {title[:120]}"
        nid = create_notification(
            user_id,
            ntype="keyword_match",
            text=text,
            details=summary[:500],
            keyword=matched_kw,
            meta={
                "article_id": article_id,
                "canonical_url": canonical,
                "matched_keyword": matched_kw,
                "post_title": title[:200],
                "source": source,
            },
            dedupe_key=f"kw:{user_id}:{canonical}:{matched_kw}",
        )
        if nid:
            sent += 1

    if sent:
        logger.info("keyword alerts sent=%s article=%s", sent, canonical[:80])
    return sent


def notify_keyword_matches_for_user_recent(
    user,
    *,
    hours: int = 168,
    limit: int = 200,
) -> int:
    """Backfill keyword alerts for one user (e.g. after saving interests)."""
    from django.contrib.auth import get_user_model

    from news.services import article_query

    User = get_user_model()
    if not isinstance(user, User):
        return 0

    keywords = article_query.list_user_keywords(user)
    if not keywords:
        return 0

    since = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    col = processed_collection()
    cursor = (
        col.find(
            {
                "processed_at": {"$gte": since},
                "canonical_url": {"$exists": True, "$ne": ""},
            }
        )
        .sort("processed_at", -1)
        .limit(max(1, min(int(limit), 500)))
    )

    sent = 0
    for doc in cursor:
        if not article_visible_to_users(doc):
            continue
        hits = _matched_keywords(doc, keywords)
        if not hits:
            continue

        canonical = str(doc.get("canonical_url") or doc.get("raw_canonical_url") or "").strip()
        article_id = str(doc.get("_id") or "")
        title = str(doc.get("title") or "New article").strip() or "New article"
        summary = str(doc.get("summary") or "").strip()
        source = str(doc.get("source_key") or "").strip()
        matched_kw = hits[0]
        if matched_kw == "your topics":
            text = f"New article in your interests: {title[:120]}"
        else:
            text = f"New article for “{matched_kw}”: {title[:120]}"

        nid = create_notification(
            user.pk,
            ntype="keyword_match",
            text=text,
            details=summary[:500],
            keyword=matched_kw if matched_kw != "your topics" else None,
            meta={
                "article_id": article_id,
                "canonical_url": canonical,
                "matched_keyword": matched_kw,
                "post_title": title[:200],
                "source": source,
            },
            dedupe_key=f"kw:{user.pk}:{canonical}:{matched_kw}",
        )
        if nid:
            sent += 1

    if sent:
        logger.info("keyword backfill user_id=%s sent=%s", user.pk, sent)
    return sent


def notify_keyword_matches_for_recent_articles(
    *,
    hours: int = 72,
    limit: int = 80,
) -> int:
    """
    After the user updates interests, alert for recently processed articles they may have missed.
    Dedupe keys prevent duplicate notifications within 24h.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
    col = processed_collection()
    cursor = (
        col.find(
            {
                "processed_at": {"$gte": since},
                "canonical_url": {"$exists": True, "$ne": ""},
            }
        )
        .sort("processed_at", -1)
        .limit(max(1, min(int(limit), 200)))
    )
    total = 0
    for doc in cursor:
        if article_visible_to_users(doc):
            total += notify_keyword_matches_for_article(doc)
    if total:
        logger.info("keyword backfill sent=%s (since %s)", total, since.isoformat())
    return total
