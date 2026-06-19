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
from news.article_media import article_image_url, hydrate_processed_image_urls
from news.article_text import build_card_summary, sanitize_article_body
from news.category_matching import (
    article_matches_category,
    interest_matches_article,
    user_follows_all_categories,
)
from news.categorization.labels import category_slug
from news.categorization.matching import article_browse_slugs, article_browse_slugs_with_fallback, browse_primary_only_enabled
from news.moderation_rules import article_visible_to_users, user_feed_visibility_clause
from news.mongo_db import processed_collection, raw_collection, reactions_collection, user_keywords_collection
from news.services.feed_cache import (
    explore_cache_key,
    get_cached_category_counts,
    get_cached_explore,
    set_cached_category_counts,
    set_cached_explore,
)

User = get_user_model()

ID_LABELS = {0: "real", 1: "fake", 2: "suspicious"}

# Fields loaded from processed_articles for feed/explore (never raw_articles).
PROCESSED_LIST_PROJECTION = {
    "_id": 1,
    "title": 1,
    "summary": 1,
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
    "primary_category": 1,
    "categories": 1,
    "category_confidence": 1,
    "image_url": 1,
}

# Extra text fields for keyword/search matching (avoid heavy match_embedding vectors).
PROCESSED_SEARCH_PROJECTION = {
    **PROCESSED_LIST_PROJECTION,
    "clean_text": 1,
    "normalized_text": 1,
    "entities": 1,
    "normalized_terms": 1,
}

# Backward-compatible alias for callers expecting the old name.
PROCESSED_FEED_PROJECTION = PROCESSED_SEARCH_PROJECTION


def _feed_batch_size(page_size: int, *, multiplier: int = 2, floor: int = 60, cap: int = 120) -> int:
    return min(max(page_size * multiplier, floor), cap)


def _oid_str(doc: dict) -> str:
    _id = doc.get("_id")
    return str(_id) if _id is not None else ""


def _hydrate_docs_images(docs: list[dict]) -> None:
    """Fill image_url on processed docs from raw_articles when missing (same as admin list)."""
    if docs:
        hydrate_processed_image_urls(docs, raw_collection())


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


def _keyword_matches_doc(doc: dict, keyword: str) -> bool:
    return interest_matches_article(doc, keyword)


def _matches_feed_filters(
    doc: dict,
    user_keywords: list[str],
    search_q: str,
) -> bool:
    if not article_visible_to_users(doc):
        return False
    hay = _doc_haystack(doc)
    if user_keywords and not user_follows_all_categories(user_keywords):
        if not any(_keyword_matches_doc(doc, k) for k in user_keywords):
            return False
    q = (search_q or "").strip().lower()
    if q and not _search_matches_hay(hay, q):
        return False
    return True


def _article_full_text(doc: dict) -> str:
    """Full article body for detail views (processed fields only)."""
    return doc.get("clean_text") or doc.get("body_text") or ""


def article_to_api_dict(doc: dict, *, for_list: bool = False) -> dict:
    """Shape for mobile/web clients. Processed documents only — no raw_articles."""
    cid = _oid_str(doc)
    title = doc.get("title") or ""
    if for_list:
        summary = (doc.get("summary") or "").strip()
        if not summary:
            summary = build_card_summary(
                title=title,
                stored_summary="",
                body=_article_full_text(doc),
            )
        full_text = ""
    else:
        full_text = sanitize_article_body(_article_full_text(doc), title=title)
        summary = build_card_summary(
            title=title,
            stored_summary=doc.get("summary") or "",
            body=_article_full_text(doc),
        )
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
        "primary_category": doc.get("primary_category") or "",
        "categories": list(doc.get("categories") or []),
        "category_confidence": doc.get("category_confidence"),
        "credibility_label": label,
        "credibility_label_name": doc.get("credibility_label_name")
        or (labels_map.get(label, labels_map.get(str(label))) if isinstance(labels_map, dict) else None),
        "image_url": article_image_url(doc),
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


def _search_query_tokens(search_q: str) -> list[str]:
    q = (search_q or "").strip().lower()
    if not q:
        return []
    return [w for w in re.findall(r"[a-z0-9]+", q) if len(w) >= 2][:8]


def _search_matches_hay(hay: str, search_q: str) -> bool:
    """True when full phrase or every query token appears in article text."""
    q = (search_q or "").strip().lower()
    if not q:
        return True
    blob = (hay or "").lower()
    if q in blob:
        return True
    tokens = _search_query_tokens(q)
    if not tokens:
        return q in blob
    if len(tokens) == 1:
        return tokens[0] in blob
    return all(t in blob for t in tokens)


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


def _category_browse_query_clause(category: str) -> dict:
    """Mongo pre-filter for category browse (primary only or primary + ML labels)."""
    cat_key = category_slug((category or "").strip())
    if not cat_key:
        return {}
    if browse_primary_only_enabled():
        return {"primary_category": cat_key}
    return {"$or": [{"primary_category": cat_key}, {"categories": cat_key}]}


def get_primary_category_counts(*, force_refresh: bool = False) -> dict[str, int]:
    """Count visible processed articles per browse category (cached, ML + rule fallback)."""
    if not force_refresh:
        cached = get_cached_category_counts()
        if cached is not None:
            return cached

    proc = processed_collection()
    out: dict[str, int] = {}
    try:
        pipeline = [
            {
                "$match": {
                    **user_feed_visibility_clause(),
                    "primary_category": {"$exists": True, "$nin": ["", None]},
                }
            },
            {"$group": {"_id": "$primary_category", "n": {"$sum": 1}}},
        ]
        for row in proc.aggregate(pipeline):
            slug = category_slug(row.get("_id") or "")
            if slug:
                out[slug] = out.get(slug, 0) + int(row.get("n") or 0)
    except Exception:
        out = {}

    projection = {
        "title": 1,
        "summary": 1,
        "clean_text": 1,
        "normalized_text": 1,
        "source_key": 1,
        "topic_keywords": 1,
        "normalized_terms": 1,
        "entities": 1,
        "primary_category": 1,
        "categories": 1,
        "category_scores": 1,
        "credibility_label": 1,
        "credibility_label_name": 1,
        "moderation_status": 1,
    }
    unlabeled_query = _merge_query(
        user_feed_visibility_clause(),
        {
            "$or": [
                {"primary_category": {"$exists": False}},
                {"primary_category": ""},
                {"primary_category": None},
            ]
        },
    )
    for doc in proc.find(unlabeled_query, projection):
        if not article_visible_to_users(doc):
            continue
        for slug in article_browse_slugs_with_fallback(doc):
            out[slug] = out.get(slug, 0) + 1

    set_cached_category_counts(out)
    return out


def _merge_query(*parts: dict) -> dict:
    clauses = [p for p in parts if p]
    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


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
    batch_size = _feed_batch_size(page_size)
    query = _merge_query(
        _query_after_cursor(cursor or ""),
        _search_filter_clause(q),
        user_feed_visibility_clause(),
    )

    out: list[dict] = []
    last_seen_doc: Optional[dict] = None
    last_batch_len = 0

    while len(out) < page_size:
        docs = list(
            proc.find(query, PROCESSED_SEARCH_PROJECTION)
            .sort([("processed_at", -1), ("_id", -1)])
            .limit(batch_size)
        )
        last_batch_len = len(docs)
        if not docs:
            break

        _hydrate_docs_images(docs)

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
        query = _merge_query(
            _query_after_cursor(next_scan_cursor),
            _search_filter_clause(q),
            user_feed_visibility_clause(),
        )

    next_cursor = _cursor_payload_from_doc(last_seen_doc or {})
    has_more = False
    if next_cursor and last_seen_doc is not None and last_batch_len >= batch_size:
        if len(out) >= page_size:
            has_more = True
        else:
            probe_query = _merge_query(
                _query_after_cursor(next_cursor),
                _search_filter_clause(q),
                user_feed_visibility_clause(),
            )
            while True:
                docs = list(
                    proc.find(probe_query, PROCESSED_SEARCH_PROJECTION)
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
                probe_query = _merge_query(
                    _query_after_cursor(next_scan_cursor),
                    _search_filter_clause(q),
                    user_feed_visibility_clause(),
                )

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
    if doc is None or not article_visible_to_users(doc):
        return None
    _hydrate_docs_images([doc])
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


def search_processed_articles(search_q: str, *, limit: int = 10) -> list[dict]:
    """
    Search processed_articles in MongoDB for chatbot / in-app lookup.
    Returns API-shaped article dicts sorted by relevance to the query.
    """
    q = (search_q or "").strip()
    if not q:
        return []

    proc = processed_collection()
    words = [w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) >= 2][:8]
    or_clauses: list[dict] = list(_search_filter_clause(q).get("$or") or [])
    for word in words:
        escaped = re.escape(word)
        or_clauses.extend(
            [
                {"title": {"$regex": escaped, "$options": "i"}},
                {"summary": {"$regex": escaped, "$options": "i"}},
                {"topic_keywords": {"$regex": escaped, "$options": "i"}},
            ]
        )
    if not or_clauses:
        return []

    docs = list(
        proc.find(
            _merge_query({"$or": or_clauses}, user_feed_visibility_clause()),
            PROCESSED_FEED_PROJECTION,
        )
        .sort("processed_at", -1)
        .limit(max(limit * 4, 24))
    )
    if not docs:
        return []

    _hydrate_docs_images(docs)
    docs = [d for d in docs if article_visible_to_users(d)]
    if not docs:
        return []
    q_lower = q.lower()
    word_set = set(words)

    def _score(doc: dict) -> float:
        hay = _doc_haystack(doc)
        score = 0.0
        title = str(doc.get("title") or "").lower()
        if q_lower in title:
            score += 8.0
        if q_lower in hay:
            score += 4.0
        for w in word_set:
            if w in title:
                score += 2.0
            elif w in hay:
                score += 1.0
        dt = doc.get("processed_at")
        if isinstance(dt, datetime):
            age_h = max(0.0, (datetime.now(timezone.utc) - (
                dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            )).total_seconds() / 3600.0)
            score += 1.0 / (1.0 + age_h / 24.0)
        return score

    ranked = sorted(docs, key=_score, reverse=True)
    return [article_to_api_dict(d, for_list=True) for d in ranked[:limit]]


def get_recent_processed_articles(*, limit: int = 10) -> list[dict]:
    """Latest articles from processed_articles for headline-style chatbot queries."""
    page_size = max(1, min(int(limit or 10), 30))
    docs = list(
        processed_collection()
        .find(user_feed_visibility_clause(), PROCESSED_FEED_PROJECTION)
        .sort("processed_at", -1)
        .limit(page_size)
    )
    if not docs:
        return []
    _hydrate_docs_images(docs)
    docs = [d for d in docs if article_visible_to_users(d)]
    return [article_to_api_dict(d, for_list=True) for d in docs]


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
    if not docs or take <= 0:
        return []
    scored = [(doc, _explore_rank_score(doc, now)) for doc in docs]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    source_counts: dict[str, int] = {}
    chosen: list[dict] = []
    remaining = scored[:]
    while remaining and len(chosen) < take:
        best_idx = 0
        best_val = float("-inf")
        for idx, (doc, base_score) in enumerate(remaining):
            src = str(doc.get("source_key") or "unknown")
            diversity_penalty = 0.12 * source_counts.get(src, 0)
            val = base_score - diversity_penalty
            if val > best_val:
                best_val = val
                best_idx = idx
        picked, _ = remaining.pop(best_idx)
        src = str(picked.get("source_key") or "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1
        chosen.append(picked)
    return chosen


def _doc_has_display_image(doc: dict) -> bool:
    return bool(article_image_url(doc))


def get_pics_feed(
    limit: int = 50,
    *,
    search_q: str = "",
) -> list[dict]:
    page = get_pics_feed_page(limit=limit, search_q=search_q, cursor=None)
    return page["results"]


def get_pics_feed_page(
    *,
    limit: int = 30,
    search_q: str = "",
    cursor: Optional[str] = None,
) -> dict[str, Any]:
    """Cursor-based feed of articles with hero images (for Pics / visual browse)."""
    q = (search_q or "").strip().lower()
    page_size = max(1, min(int(limit or 30), 200))
    proc = processed_collection()
    batch_size = _feed_batch_size(page_size, multiplier=3, cap=150)
    query = _merge_query(
        _query_after_cursor(cursor or ""),
        _search_filter_clause(q),
        user_feed_visibility_clause(),
    )

    out: list[dict] = []
    last_seen_doc: Optional[dict] = None
    now = datetime.now(timezone.utc)
    last_batch_len = 0
    list_projection = PROCESSED_LIST_PROJECTION if not q else PROCESSED_SEARCH_PROJECTION

    while len(out) < page_size:
        fetched = list(
            proc.find(query, list_projection)
            .sort([("processed_at", -1), ("_id", -1)])
            .limit(batch_size)
        )
        last_batch_len = len(fetched)
        if not fetched:
            break

        _hydrate_docs_images(fetched)
        docs = [d for d in fetched if article_visible_to_users(d) and _doc_has_display_image(d)]
        last_seen_doc = fetched[-1]

        if not q:
            ranked_batch = _rank_for_diversity(docs, now, take=page_size - len(out))
            for doc in ranked_batch:
                out.append(article_to_api_dict(doc, for_list=True))
                if len(out) >= page_size:
                    break
            if len(out) >= page_size or last_batch_len < batch_size:
                break
            next_scan_cursor = _cursor_payload_from_doc(fetched[-1])
            if not next_scan_cursor:
                break
            query = _merge_query(
                _query_after_cursor(next_scan_cursor),
                _search_filter_clause(q),
                user_feed_visibility_clause(),
            )
            continue

        filtered: list[dict] = []
        for doc in docs:
            hay = _doc_haystack(doc)
            if not _search_matches_hay(hay, q):
                continue
            filtered.append(doc)

        ranked_batch = _rank_for_diversity(filtered, now, take=page_size - len(out))
        for doc in ranked_batch:
            out.append(article_to_api_dict(doc, for_list=True))
            if len(out) >= page_size:
                break

        if len(out) >= page_size:
            break

        next_scan_cursor = _cursor_payload_from_doc(fetched[-1])
        if not next_scan_cursor:
            break
        query = _merge_query(
            _query_after_cursor(next_scan_cursor),
            _search_filter_clause(q),
            user_feed_visibility_clause(),
        )

    next_cursor = _cursor_payload_from_doc(last_seen_doc or {})
    has_more = bool(next_cursor and last_batch_len >= batch_size)
    hydrate_article_reaction_counts(out)
    return {"results": out, "next_cursor": next_cursor if has_more else None, "has_more": has_more}


def get_explore_feed_page(
    *,
    limit: int = 30,
    search_q: str = "",
    category: str = "",
    cursor: Optional[str] = None,
) -> dict[str, Any]:
    """Cursor-based Explore page; optional category slug filters matches."""
    q = (search_q or "").strip().lower()
    cat = (category or "").strip()
    page_size = max(1, min(int(limit or 30), 200))
    cacheable = not q and not cat
    cache_key = explore_cache_key(limit=page_size, q=q, cursor=cursor)
    if cacheable:
        cached = get_cached_explore(cache_key)
        if cached is not None:
            hydrate_article_reaction_counts(cached.get("results") or [])
            return cached

    proc = processed_collection()
    batch_size = _feed_batch_size(page_size)
    cat_key = category_slug(cat) if cat else ""
    category_total = None
    if cat_key and not cursor:
        category_total = get_primary_category_counts().get(cat_key, 0)
    query = _merge_query(
        _query_after_cursor(cursor or ""),
        _search_filter_clause(q),
        user_feed_visibility_clause(),
        _category_browse_query_clause(cat_key) if cat_key else {},
    )

    out: list[dict] = []
    last_seen_doc: Optional[dict] = None
    now = datetime.now(timezone.utc)
    last_batch_len = 0
    list_projection = PROCESSED_LIST_PROJECTION if not q and not cat else PROCESSED_SEARCH_PROJECTION

    while len(out) < page_size:
        docs = list(
            proc.find(query, list_projection)
            .sort([("processed_at", -1), ("_id", -1)])
            .limit(batch_size)
        )
        last_batch_len = len(docs)
        if not docs:
            break

        _hydrate_docs_images(docs)
        docs = [d for d in docs if article_visible_to_users(d)]

        if not q and not cat:
            ranked_batch = _rank_for_diversity(docs[:page_size], now, take=page_size)
            for doc in ranked_batch:
                out.append(article_to_api_dict(doc, for_list=True))
            last_seen_doc = docs[min(page_size, len(docs)) - 1]
            break

        filtered: list[dict] = []
        for doc in docs:
            last_seen_doc = doc
            if not article_visible_to_users(doc):
                continue
            if q:
                hay = _doc_haystack(doc)
                if not _search_matches_hay(hay, q):
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
        query = _merge_query(
            _query_after_cursor(next_scan_cursor),
            _search_filter_clause(q),
            user_feed_visibility_clause(),
            _category_browse_query_clause(cat_key) if cat_key else {},
        )

    next_cursor = _cursor_payload_from_doc(last_seen_doc or {})
    has_more = bool(next_cursor and last_batch_len >= batch_size)
    if has_more and not q and not cat and len(out) < page_size:
        has_more = last_batch_len >= batch_size

    hydrate_article_reaction_counts(out)
    page = {"results": out, "next_cursor": next_cursor if has_more else None, "has_more": has_more}
    if category_total is not None:
        page["category_total"] = category_total
    if cacheable:
        set_cached_explore(cache_key, page, cursor=cursor)
    return page
