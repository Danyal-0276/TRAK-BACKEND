from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from django.contrib.auth import get_user_model

from news.mongo_db import processed_collection, raw_collection, user_keywords_collection

User = get_user_model()

ID_LABELS = {0: "real", 1: "fake", 2: "suspicious"}


def _oid_str(doc: dict) -> str:
    _id = doc.get("_id")
    return str(_id) if _id is not None else ""


def _normalize_keywords(user: User) -> list[str]:
    col = user_keywords_collection()
    row = col.find_one({"user_id": user.pk})
    if not row:
        return []
    kws = row.get("keywords") or []
    return [str(k).strip().lower() for k in kws if str(k).strip()]


def _doc_haystack(doc: dict, raw_fallback: Optional[dict] = None) -> str:
    """All text + extracted topic tokens + entities — used for keyword + search matching."""
    parts: list[str] = [
        str(doc.get("title") or ""),
        str(doc.get("summary") or ""),
        str(doc.get("clean_text") or ""),
        str(doc.get("normalized_text") or ""),
    ]
    raw = raw_fallback or {}
    parts.append(str(raw.get("body_text") or "")[:4000])
    parts.append(str(raw.get("title") or ""))
    for k in doc.get("topic_keywords") or []:
        parts.append(str(k))
    for t in doc.get("normalized_terms") or []:
        parts.append(str(t))
    for e in doc.get("entities") or []:
        if isinstance(e, dict):
            parts.append(str(e.get("text") or ""))
    return " ".join(parts).lower()


def _matches_feed_filters(
    doc: dict,
    raw_fallback: Optional[dict],
    user_keywords: list[str],
    search_q: str,
) -> bool:
    hay = _doc_haystack(doc, raw_fallback)
    if user_keywords and not any(k in hay for k in user_keywords):
        return False
    q = (search_q or "").strip().lower()
    if q and q not in hay:
        return False
    return True


def article_to_api_dict(doc: dict, raw_fallback: Optional[dict] = None) -> dict:
    """Shape for mobile/web clients."""
    cid = _oid_str(doc)
    title = doc.get("title") or (raw_fallback or {}).get("title") or ""
    body = doc.get("summary") or doc.get("clean_text") or (raw_fallback or {}).get("body_text") or ""
    source = doc.get("source_key") or (raw_fallback or {}).get("source_key") or ""
    published = doc.get("published_at") or (raw_fallback or {}).get("published_at")
    if isinstance(published, datetime):
        published = published.isoformat()
    label = doc.get("credibility_label")
    labels_map = doc.get("credibility_labels_map") or ID_LABELS
    prob = doc.get("credibility_max_prob")
    return {
        "id": cid,
        "title": title,
        "excerpt": (body[:280] + "…") if len(body) > 280 else body,
        "content": body,
        "source": source,
        "published_at": published,
        "canonical_url": doc.get("canonical_url") or (raw_fallback or {}).get("canonical_url"),
        "credibility": {
            "label_code": label,
            "label": labels_map.get(label, labels_map.get(str(label))) if isinstance(labels_map, dict) else None,
            "max_prob": prob,
            "probs": doc.get("credibility_probs"),
        },
        "entities": doc.get("entities") or [],
        "topic_keywords": doc.get("topic_keywords") or [],
    }


def get_user_feed(
    user: User,
    limit: int = 50,
    *,
    search_q: str = "",
) -> list[dict]:
    """
    Personalized feed: if the user saved keywords, only articles whose haystack
    (title, body, topic_keywords, entities, …) matches at least one keyword.
    Optional search_q further narrows (substring in haystack).
    """
    keywords = _normalize_keywords(user)
    q = (search_q or "").strip()
    proc = processed_collection()
    scan = limit * 4 if (keywords or q) else limit
    cursor = proc.find().sort("processed_at", -1).limit(max(scan, limit))
    raw_col = raw_collection()
    out: list[dict] = []
    for doc in cursor:
        raw_doc = None
        url = doc.get("canonical_url") or doc.get("raw_canonical_url")
        if url:
            raw_doc = raw_col.find_one({"canonical_url": url})
        if not _matches_feed_filters(doc, raw_doc, keywords, q):
            continue
        out.append(article_to_api_dict(doc, raw_doc))
        if len(out) >= limit:
            break
    if not out and not keywords and not q:
        # No processed docs yet — surface recent raw articles
        for raw_doc in raw_col.find().sort("fetched_at", -1).limit(limit):
            stub = {
                "_id": raw_doc.get("_id"),
                "title": raw_doc.get("title"),
                "summary": (raw_doc.get("body_text") or "")[:500],
                "clean_text": raw_doc.get("body_text"),
                "canonical_url": raw_doc.get("canonical_url"),
                "source_key": raw_doc.get("source_key"),
                "published_at": raw_doc.get("published_at"),
                "credibility_label": None,
            }
            out.append(article_to_api_dict(stub, raw_doc))
    return out


def get_article_by_id(article_id: str, user: User) -> Optional[dict]:
    """Load processed article by Mongo _id or by canonical_url."""
    proc = processed_collection()
    raw_col = raw_collection()
    doc = None
    raw_doc = None
    if ObjectId.is_valid(article_id):
        doc = proc.find_one({"_id": ObjectId(article_id)})
    if doc is None:
        doc = proc.find_one({"canonical_url": article_id})
    if doc is None:
        if ObjectId.is_valid(article_id):
            raw_doc = raw_col.find_one({"_id": ObjectId(article_id)})
        if raw_doc is None:
            raw_doc = raw_col.find_one({"canonical_url": article_id})
        if raw_doc:
            stub = {
                "_id": raw_doc.get("_id"),
                "title": raw_doc.get("title"),
                "summary": raw_doc.get("body_text"),
                "clean_text": raw_doc.get("body_text"),
                "canonical_url": raw_doc.get("canonical_url"),
                "source_key": raw_doc.get("source_key"),
                "published_at": raw_doc.get("published_at"),
            }
            return article_to_api_dict(stub, raw_doc)
        return None
    url = doc.get("canonical_url") or doc.get("raw_canonical_url")
    if url:
        raw_doc = raw_col.find_one({"canonical_url": url})
    return article_to_api_dict(doc, raw_doc)


def upsert_user_keywords(user: User, keywords: list[str]) -> dict[str, Any]:
    col = user_keywords_collection()
    cleaned = []
    for k in keywords:
        s = re.sub(r"\s+", " ", str(k).strip().lower())
        if s and s not in cleaned:
            cleaned.append(s)
    from datetime import timezone

    now = datetime.now(timezone.utc)
    col.update_one(
        {"user_id": user.pk},
        {"$set": {"keywords": cleaned, "updated_at": now}, "$setOnInsert": {"user_id": user.pk, "created_at": now}},
        upsert=True,
    )
    return {"user_id": user.pk, "keywords": cleaned}
