"""Notify users when a newly processed article matches their saved keywords."""

from __future__ import annotations

import logging

from news.mongo_db import user_keywords_collection
from news.services.article_query import _doc_haystack, _keyword_matches_hay
from notifications.delivery import create_notification

logger = logging.getLogger(__name__)


def _matched_keywords(doc: dict, keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    hay = _doc_haystack(doc)
    return [k for k in keywords if _keyword_matches_hay(k, hay)]


def notify_keyword_matches_for_article(processed_doc: dict) -> int:
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
        hits = _matched_keywords(processed_doc, keywords)
        if not hits:
            continue

        matched_kw = hits[0]
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
            },
            dedupe_key=f"kw:{user_id}:{canonical}:{matched_kw}",
        )
        if nid:
            sent += 1

    if sent:
        logger.info("keyword alerts sent=%s article=%s", sent, canonical[:80])
    return sent
