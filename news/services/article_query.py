from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from django.contrib.auth import get_user_model

from news.mongo_db import processed_collection, raw_collection, reactions_collection, user_keywords_collection

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


def _keyword_matches_hay(keyword: str, hay: str) -> bool:
    k = str(keyword or "").strip().lower()
    if len(k) < 2:
        return False
    variants = {k, k.replace(" ", "-"), k.replace("-", " ")}
    return any(len(v) >= 2 and v in hay for v in variants)


def _matches_feed_filters(
    doc: dict,
    raw_fallback: Optional[dict],
    user_keywords: list[str],
    search_q: str,
) -> bool:
    hay = _doc_haystack(doc, raw_fallback)
    if user_keywords and not any(_keyword_matches_hay(k, hay) for k in user_keywords):
        return False
    q = (search_q or "").strip().lower()
    if q and q not in hay:
        return False
    return True


def _article_full_text(doc: dict, raw_fallback: Optional[dict] = None) -> str:
    """Full article body for detail views."""
    raw = raw_fallback or {}
    return (
        doc.get("clean_text")
        or raw.get("body_text")
        or doc.get("body_text")
        or ""
    )


def _article_card_summary(doc: dict, raw_fallback: Optional[dict] = None) -> str:
    """Short summary for feed cards (pipeline extractive summary)."""
    raw = raw_fallback or {}
    return doc.get("summary") or raw.get("summary") or ""


def article_to_api_dict(doc: dict, raw_fallback: Optional[dict] = None) -> dict:
    """Shape for mobile/web clients."""
    cid = _oid_str(doc)
    title = doc.get("title") or (raw_fallback or {}).get("title") or ""
    summary = _article_card_summary(doc, raw_fallback)
    full_text = _article_full_text(doc, raw_fallback)
    if not summary and full_text:
        parts = re.split(r"(?<=[.!?])\s+", full_text.strip())
        summary = " ".join(parts[:2]) if parts else full_text[:400]
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
        "summary": summary,
        "excerpt": summary,
        "content": full_text,
        "full_content": full_text,
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
        "like_count": 0,
        "dislike_count": 0,
    }


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
    raw_col = raw_collection()
    page_size = max(1, min(int(limit or 30), 100))
    batch_size = max(page_size * 4, 80)
    query = _query_after_cursor(cursor or "")

    out: list[dict] = []
    last_seen_doc: Optional[dict] = None

    while len(out) < page_size:
        docs = list(
            proc.find(query)
            .sort([("processed_at", -1), ("_id", -1)])
            .limit(batch_size)
        )
        if not docs:
            break

        for doc in docs:
            last_seen_doc = doc
            raw_doc = None
            url = doc.get("canonical_url") or doc.get("raw_canonical_url")
            if url:
                raw_doc = raw_col.find_one({"canonical_url": url})
            if not _matches_feed_filters(doc, raw_doc, keywords, q):
                continue
            out.append(article_to_api_dict(doc, raw_doc))
            if len(out) >= page_size:
                break

        if len(out) >= page_size:
            break

        next_scan_cursor = _cursor_payload_from_doc(docs[-1])
        if not next_scan_cursor:
            break
        query = _query_after_cursor(next_scan_cursor)

    next_cursor = _cursor_payload_from_doc(last_seen_doc or {})
    has_more = False
    if next_cursor and last_seen_doc is not None:
        probe_query = _query_after_cursor(next_cursor)
        while True:
            docs = list(
                proc.find(probe_query)
                .sort([("processed_at", -1), ("_id", -1)])
                .limit(batch_size)
            )
            if not docs:
                break
            for doc in docs:
                raw_doc = None
                url = doc.get("canonical_url") or doc.get("raw_canonical_url")
                if url:
                    raw_doc = raw_col.find_one({"canonical_url": url})
                if _matches_feed_filters(doc, raw_doc, keywords, q):
                    has_more = True
                    break
            if has_more:
                break
            next_scan_cursor = _cursor_payload_from_doc(docs[-1])
            if not next_scan_cursor:
                break
            probe_query = _query_after_cursor(next_scan_cursor)

    hydrate_article_reaction_counts(out)
    return {"results": out, "next_cursor": next_cursor if has_more else None, "has_more": has_more}


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
            item = article_to_api_dict(stub, raw_doc)
            hydrate_article_reaction_counts([item])
            return item
        return None
    url = doc.get("canonical_url") or doc.get("raw_canonical_url")
    if url:
        raw_doc = raw_col.find_one({"canonical_url": url})
    item = article_to_api_dict(doc, raw_doc)
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
    from datetime import timezone

    now = datetime.now(timezone.utc)
    col.update_one(
        {"user_id": user.pk},
        {"$set": {"keywords": cleaned, "updated_at": now}, "$setOnInsert": {"user_id": user.pk, "created_at": now}},
        upsert=True,
    )
    return {"user_id": user.pk, "keywords": cleaned}


def get_explore_feed(limit: int = 200, *, search_q: str = "") -> list[dict]:
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
    """
    Lightweight ranking score for Explore.
    - Recency: newer articles rank higher.
    - Credibility: real/high-confidence > suspicious > fake.
    """
    processed_at = doc.get("processed_at")
    if isinstance(processed_at, datetime):
        dt = processed_at if processed_at.tzinfo else processed_at.replace(tzinfo=timezone.utc)
    else:
        dt = now
    age_hours = max(0.0, (now - dt).total_seconds() / 3600.0)
    recency_score = 1.0 / (1.0 + age_hours / 12.0)  # half-life-ish around 12 hours

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
    """
    Greedy rerank to diversify sources (similar to social feed source-mixing).
    """
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
    """Cursor-based Explore page for infinite scrolling."""
    q = (search_q or "").strip().lower()
    proc = processed_collection()
    raw_col = raw_collection()

    page_size = max(1, min(int(limit or 30), 200))
    batch_size = max(page_size * 4, 80)
    query = _query_after_cursor(cursor or "")

    out: list[dict] = []
    last_seen_doc: Optional[dict] = None
    now = datetime.now(timezone.utc)

    while len(out) < page_size:
        docs = list(
            proc.find(query)
            .sort([("processed_at", -1), ("_id", -1)])
            .limit(batch_size)
        )
        if not docs:
            break
        # No search filter: keep cursor pagination strict so every article appears eventually.
        if not q:
            ranked_batch = _rank_for_diversity(docs[:page_size], now, take=page_size)
            for doc in ranked_batch:
                raw_doc = None
                url = doc.get("canonical_url") or doc.get("raw_canonical_url")
                if url:
                    raw_doc = raw_col.find_one({"canonical_url": url})
                out.append(article_to_api_dict(doc, raw_doc))
            last_seen_doc = docs[min(page_size, len(docs)) - 1]
            break

        filtered: list[dict] = []
        for doc in docs:
            last_seen_doc = doc
            if q:
                raw_doc = None
                url = doc.get("canonical_url") or doc.get("raw_canonical_url")
                if url:
                    raw_doc = raw_col.find_one({"canonical_url": url})
                hay = _doc_haystack(doc, raw_doc)
                if q not in hay:
                    continue
            filtered.append(doc)

        ranked_batch = _rank_for_diversity(filtered, now, take=page_size - len(out))
        for doc in ranked_batch:
            raw_doc = None
            url = doc.get("canonical_url") or doc.get("raw_canonical_url")
            if url:
                raw_doc = raw_col.find_one({"canonical_url": url})
            out.append(article_to_api_dict(doc, raw_doc))
            if len(out) >= page_size:
                break
        # advance scan window to avoid rescanning already-considered documents
        next_scan_cursor = _cursor_payload_from_doc(docs[-1])
        if not next_scan_cursor:
            break
        query = _query_after_cursor(next_scan_cursor)

    next_cursor = _cursor_payload_from_doc(last_seen_doc or {})
    has_more = False
    if next_cursor:
        has_more = (
            proc.find_one(
                _query_after_cursor(next_cursor),
                sort=[("processed_at", -1), ("_id", -1)],
            )
            is not None
        )
    hydrate_article_reaction_counts(out)
    return {"results": out, "next_cursor": next_cursor if has_more else None, "has_more": has_more}
