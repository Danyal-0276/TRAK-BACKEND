from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from django.contrib.auth import get_user_model

from news.credibility.score import (
    compute_credibility_score_from_doc,
    effective_credibility_probs,
)
from news.mongo_db import processed_collection, reactions_collection, user_keywords_collection
from news.services.feed_cache import explore_cache_key, get_cached_explore, set_cached_explore

User = get_user_model()

ID_LABELS = {0: "real", 1: "fake", 2: "suspicious"}

# Fields loaded from processed_articles for feed/explore (never raw_articles).
PROCESSED_FEED_PROJECTION = {
    "_id": 1,
    "title": 1,
    "summary": 1,
    "clean_text": 1,
    "normalized_text": 1,
    "canonical_url": 1,
    "raw_canonical_url": 1,
    "source_key": 1,
    "published_at": 1,
    "processed_at": 1,
    "credibility_label": 1,
    "credibility_max_prob": 1,
    "credibility_probs": 1,
    "credibility_labels_map": 1,
    "topic_keywords": 1,
    "entities": 1,
}


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


def _doc_haystack(doc: dict) -> str:
    """Processed-only text for keyword + search matching."""
    parts: list[str] = [
        str(doc.get("title") or ""),
        str(doc.get("summary") or ""),
        str(doc.get("clean_text") or "")[:4000],
        str(doc.get("normalized_text") or "")[:2000],
    ]
    for k in doc.get("topic_keywords") or []:
        parts.append(str(k))
    for t in doc.get("normalized_terms") or []:
        parts.append(str(t))
    for e in doc.get("entities") or []:
        if isinstance(e, dict):
            parts.append(str(e.get("text") or ""))
    return " ".join(parts).lower()


def _keyword_matches_hay(keyword: str, hay: str) -> bool:
    k = str(keyword or "").strip().lower()
    if len(k) < 2:
        return False
    variants = {k, k.replace(" ", "-"), k.replace("-", " ")}
    return any(len(v) >= 2 and v in hay for v in variants)


def _matches_feed_filters(
    doc: dict,
    user_keywords: list[str],
    search_q: str,
) -> bool:
    hay = _doc_haystack(doc)
    if user_keywords and not any(_keyword_matches_hay(k, hay) for k in user_keywords):
        return False
    q = (search_q or "").strip().lower()
    if q and q not in hay:
        return False
    return True


def _article_full_text(doc: dict) -> str:
    """Full article body for detail views (processed fields only)."""
    return doc.get("clean_text") or doc.get("body_text") or ""


def _article_card_summary(doc: dict) -> str:
    """Short summary for feed cards."""
    return doc.get("summary") or ""


def article_to_api_dict(doc: dict, *, for_list: bool = False) -> dict:
    """Shape for mobile/web clients. Processed documents only — no raw_articles."""
    cid = _oid_str(doc)
    title = doc.get("title") or ""
    summary = _article_card_summary(doc)
    full_text = _article_full_text(doc)
    if not summary and full_text:
        parts = re.split(r"(?<=[.!?])\s+", full_text.strip())
        summary = " ".join(parts[:2]) if parts else full_text[:400]
    source = doc.get("source_key") or ""
    published = doc.get("published_at")
    if isinstance(published, datetime):
        published = published.isoformat()
    label = doc.get("credibility_label")
    labels_map = doc.get("credibility_labels_map") or ID_LABELS
    probs = effective_credibility_probs(doc) or doc.get("credibility_probs")
    prob = doc.get("credibility_max_prob")
    if label is not None and isinstance(probs, list):
        try:
            idx = int(label)
            if 0 <= idx < len(probs):
                prob = float(probs[idx])
        except (TypeError, ValueError):
            pass
    payload: dict[str, Any] = {
        "id": cid,
        "title": title,
        "summary": summary,
        "excerpt": summary,
        "source": source,
        "published_at": published,
        "canonical_url": doc.get("canonical_url") or doc.get("raw_canonical_url"),
        "credibility": {
            "label_code": label,
            "label": labels_map.get(label, labels_map.get(str(label))) if isinstance(labels_map, dict) else None,
            "max_prob": prob,
            "score": compute_credibility_score_from_doc(doc),
            "probs": probs,
            "fake_detection_label": doc.get("fake_detection_label"),
            "fact_check_verdict": doc.get("fact_check_verdict"),
            "fact_check_hits": doc.get("fact_check_hits"),
            "fact_check_providers": doc.get("fact_check_providers_used") or [],
            "fact_check_results": doc.get("fact_check_results") or [],
            "fact_check_ratings": doc.get("fact_check_textual_ratings") or [],
            "fact_check_sources": doc.get("fact_check_urls") or [],
        },
        "topic_keywords": doc.get("topic_keywords") or [],
        "like_count": 0,
        "dislike_count": 0,
    }
    if not for_list:
        payload["content"] = full_text
        payload["full_content"] = full_text
        payload["entities"] = doc.get("entities") or []
    return payload


def hydrate_article_reaction_counts(items: list[dict]) -> None:
    """Attach like_count / dislike_count (and legacy upvotes) from reactions collection."""
    if not items:
        return
    ids: list[str] = []
    for it in items:
        aid = str(it.get("id") or "").strip()
        if aid:
            ids.append(aid)
    if not ids:
        return
    coll = reactions_collection()
    tallies: dict[str, dict[str, int]] = {i: {"likes": 0, "dislikes": 0} for i in ids}
    try:
        for row in coll.aggregate(
            [
                {"$match": {"article_id": {"$in": ids}}},
                {"$group": {"_id": {"aid": "$article_id", "r": "$reaction"}, "c": {"$sum": 1}}},
            ]
        ):
            aid = str(row["_id"].get("aid") or "")
            r = str(row["_id"].get("r") or "")
            c = int(row.get("c") or 0)
            if aid not in tallies:
                tallies[aid] = {"likes": 0, "dislikes": 0}
            if r == "like":
                tallies[aid]["likes"] = c
            elif r == "dislike":
                tallies[aid]["dislikes"] = c
    except Exception:
        return
    for it in items:
        aid = str(it.get("id") or "").strip()
        t = tallies.get(aid, {"likes": 0, "dislikes": 0})
        it["like_count"] = t["likes"]
        it["dislike_count"] = t["dislikes"]
        it["upvotes"] = t["likes"]


def _search_filter_clause(q: str) -> dict:
    """Mongo pre-filter for explore search (processed fields only)."""
    escaped = re.escape(q.strip())
    if not escaped:
        return {}
    return {
        "$or": [
            {"title": {"$regex": escaped, "$options": "i"}},
            {"summary": {"$regex": escaped, "$options": "i"}},
            {"clean_text": {"$regex": escaped, "$options": "i"}},
            {"topic_keywords": {"$regex": escaped, "$options": "i"}},
        ]
    }


def _merge_query(base: dict, extra: dict) -> dict:
    if not extra:
        return base
    if not base:
        return extra
    return {"$and": [base, extra]}


def get_user_feed(
    user: User,
    limit: int = 50,
    *,
    search_q: str = "",
) -> list[dict]:
    """Backward-compatible wrapper — returns first page results only."""
    page = get_user_feed_page(user, limit=limit, search_q=search_q, cursor=None)
    return page["results"]


def get_user_feed_page(
    user: User,
    *,
    limit: int = 30,
    search_q: str = "",
    cursor: Optional[str] = None,
) -> dict[str, Any]:
    """
    Cursor-based personalized feed for infinite scrolling.
    Same cursor format as explore: processed_at|ObjectId.
    """
    keywords = _normalize_keywords(user)
    q = (search_q or "").strip().lower()
    if not keywords and not q:
        return {"results": [], "next_cursor": None, "has_more": False}

    proc = processed_collection()
    page_size = max(1, min(int(limit or 30), 100))
    batch_size = max(page_size * 4, 80)
    query = _merge_query(_query_after_cursor(cursor or ""), _search_filter_clause(q))

    out: list[dict] = []
    last_seen_doc: Optional[dict] = None
    last_batch_len = 0

    while len(out) < page_size:
        docs = list(
            proc.find(query, PROCESSED_FEED_PROJECTION)
            .sort([("processed_at", -1), ("_id", -1)])
            .limit(batch_size)
        )
        last_batch_len = len(docs)
        if not docs:
            break

        for doc in docs:
            last_seen_doc = doc
            if not _matches_feed_filters(doc, keywords, q):
                continue
            out.append(article_to_api_dict(doc, for_list=True))
            if len(out) >= page_size:
                break

        if len(out) >= page_size:
            break

        next_scan_cursor = _cursor_payload_from_doc(docs[-1])
        if not next_scan_cursor:
            break
        query = _merge_query(_query_after_cursor(next_scan_cursor), _search_filter_clause(q))

    next_cursor = _cursor_payload_from_doc(last_seen_doc or {})
    has_more = False
    if next_cursor and last_seen_doc is not None and last_batch_len >= batch_size:
        if len(out) >= page_size:
            has_more = True
        else:
            probe_query = _merge_query(_query_after_cursor(next_cursor), _search_filter_clause(q))
            while True:
                docs = list(
                    proc.find(probe_query, PROCESSED_FEED_PROJECTION)
                    .sort([("processed_at", -1), ("_id", -1)])
                    .limit(batch_size)
                )
                if not docs:
                    break
                for doc in docs:
                    if _matches_feed_filters(doc, keywords, q):
                        has_more = True
                        break
                if has_more:
                    break
                next_scan_cursor = _cursor_payload_from_doc(docs[-1])
                if not next_scan_cursor:
                    break
                probe_query = _merge_query(_query_after_cursor(next_scan_cursor), _search_filter_clause(q))

    hydrate_article_reaction_counts(out)
    return {"results": out, "next_cursor": next_cursor if has_more else None, "has_more": has_more}


def get_article_by_id(article_id: str, user: User) -> Optional[dict]:
    """Load processed article by Mongo _id or canonical_url. No raw_articles exposure."""
    proc = processed_collection()
    doc = None
    if ObjectId.is_valid(article_id):
        doc = proc.find_one({"_id": ObjectId(article_id)})
    if doc is None:
        doc = proc.find_one({"canonical_url": article_id})
    if doc is None:
        return None
    item = article_to_api_dict(doc, for_list=False)
    hydrate_article_reaction_counts([item])
    return item


def list_user_keywords(user: User) -> list[str]:
    """Return the user's saved feed keywords (lowercased), newest source of truth from Mongo."""
    return list(_normalize_keywords(user))


def upsert_user_keywords(user: User, keywords: list[str]) -> dict[str, Any]:
    col = user_keywords_collection()
    cleaned = []
    for k in keywords:
        s = re.sub(r"\s+", " ", str(k).strip().lower())
        if s and s not in cleaned:
            cleaned.append(s)
    now = datetime.now(timezone.utc)
    col.update_one(
        {"user_id": user.pk},
        {"$set": {"keywords": cleaned, "updated_at": now}, "$setOnInsert": {"user_id": user.pk, "created_at": now}},
        upsert=True,
    )
    return {"user_id": str(user.pk), "keywords": cleaned}


def get_explore_feed(limit: int = 50, *, search_q: str = "") -> list[dict]:
    page = get_explore_feed_page(limit=limit, search_q=search_q, cursor=None)
    return page["results"]


def _cursor_payload_from_doc(doc: dict) -> Optional[str]:
    dt = doc.get("processed_at")
    oid = doc.get("_id")
    if not isinstance(dt, datetime) or not isinstance(oid, ObjectId):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"{dt.isoformat()}|{str(oid)}"


def _query_after_cursor(cursor: str) -> dict:
    try:
        dt_raw, oid_raw = cursor.split("|", 1)
        dt = datetime.fromisoformat(dt_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        oid = ObjectId(oid_raw)
        return {
            "$or": [
                {"processed_at": {"$lt": dt}},
                {"processed_at": dt, "_id": {"$lt": oid}},
            ]
        }
    except Exception:
        return {}


def _explore_rank_score(doc: dict, now: datetime) -> float:
    processed_at = doc.get("processed_at")
    if isinstance(processed_at, datetime):
        dt = processed_at if processed_at.tzinfo else processed_at.replace(tzinfo=timezone.utc)
    else:
        dt = now
    age_hours = max(0.0, (now - dt).total_seconds() / 3600.0)
    recency_score = 1.0 / (1.0 + age_hours / 12.0)

    cred = 0.55
    label = doc.get("credibility_label")
    max_prob = doc.get("credibility_max_prob")
    if label == 0:
        cred = 1.0
    elif label == 2:
        cred = 0.75
    elif label == 1:
        cred = 0.35
    if isinstance(max_prob, (int, float)):
        cred = 0.7 * cred + 0.3 * float(max_prob)
    return 0.7 * recency_score + 0.3 * cred


def _rank_for_diversity(docs: list[dict], now: datetime, take: int) -> list[dict]:
    ranked = sorted(docs, key=lambda d: _explore_rank_score(d, now), reverse=True)
    source_counts: dict[str, int] = {}
    chosen: list[dict] = []
    remaining = ranked[:]
    while remaining and len(chosen) < take:
        best_idx = 0
        best_val = float("-inf")
        for idx, doc in enumerate(remaining):
            src = str(doc.get("source_key") or "unknown")
            diversity_penalty = 0.12 * source_counts.get(src, 0)
            val = _explore_rank_score(doc, now) - diversity_penalty
            if val > best_val:
                best_val = val
                best_idx = idx
        picked = remaining.pop(best_idx)
        src = str(picked.get("source_key") or "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
        chosen.append(picked)
    return chosen


def get_explore_feed_page(
    *,
    limit: int = 30,
    search_q: str = "",
    cursor: Optional[str] = None,
) -> dict[str, Any]:
    """Cursor-based Explore page for infinite scrolling (processed_articles only)."""
    q = (search_q or "").strip().lower()
    page_size = max(1, min(int(limit or 30), 200))
    cache_key = explore_cache_key(limit=page_size, q=q, cursor=cursor)
    if not q and not cursor:
        cached = get_cached_explore(cache_key)
        if cached is not None:
            return cached

    proc = processed_collection()
    batch_size = max(page_size * 4, 80)
    query = _merge_query(_query_after_cursor(cursor or ""), _search_filter_clause(q))

    out: list[dict] = []
    last_seen_doc: Optional[dict] = None
    now = datetime.now(timezone.utc)
    last_batch_len = 0

    while len(out) < page_size:
        docs = list(
            proc.find(query, PROCESSED_FEED_PROJECTION)
            .sort([("processed_at", -1), ("_id", -1)])
            .limit(batch_size)
        )
        last_batch_len = len(docs)
        if not docs:
            break

        if not q:
            ranked_batch = _rank_for_diversity(docs[:page_size], now, take=page_size)
            for doc in ranked_batch:
                out.append(article_to_api_dict(doc, for_list=True))
            last_seen_doc = docs[min(page_size, len(docs)) - 1]
            break

        filtered: list[dict] = []
        for doc in docs:
            last_seen_doc = doc
            hay = _doc_haystack(doc)
            if q not in hay:
                continue
            filtered.append(doc)

        ranked_batch = _rank_for_diversity(filtered, now, take=page_size - len(out))
        for doc in ranked_batch:
            out.append(article_to_api_dict(doc, for_list=True))
            if len(out) >= page_size:
                break

        if len(out) >= page_size:
            break

        next_scan_cursor = _cursor_payload_from_doc(docs[-1])
        if not next_scan_cursor:
            break
        query = _merge_query(_query_after_cursor(next_scan_cursor), _search_filter_clause(q))

    next_cursor = _cursor_payload_from_doc(last_seen_doc or {})
    has_more = bool(next_cursor and last_batch_len >= batch_size)
    if has_more and not q and len(out) < page_size:
        has_more = last_batch_len >= batch_size

    hydrate_article_reaction_counts(out)
    page = {"results": out, "next_cursor": next_cursor if has_more else None, "has_more": has_more}
    if not q and not cursor:
        set_cached_explore(cache_key, page)
    return page
