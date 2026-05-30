"""
Process raw_articles with pipeline_status=pending → processed_articles + done/failed.
Stages: clean text → credibility → BART summary (HF) → NER (names/places/orgs).
"""

from __future__ import annotations

import re
import string
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo import ReturnDocument

from news.credibility.inference import predict_credibility
from news.mongo_db import processed_collection, raw_collection
from news.notifications.keyword_alerts import notify_keyword_matches_for_article
from news.pipeline.keywords import extract_topic_keywords
from news.pipeline.ner import extract_entities, ner_model_id
from news.summarization.inference import summarize_text

# Shared counters for parallel run_batch (reset per batch).
_batch_lock = threading.Lock()


def clean_text(text: str) -> str:
    """Clean HTML/URLs; keep paragraph breaks as blank lines for display."""
    text = text or ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"http\S+", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n+", text):
        line = re.sub(r"[ \t]+", " ", block)
        line = re.sub(r"\n+", " ", line).strip()
        if line:
            paragraphs.append(line)
    if paragraphs:
        return "\n\n".join(paragraphs)
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_matching(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"http\S+", " ", text)
    text = text.translate(str.maketrans({c: " " for c in string.punctuation}))
    text = re.sub(r"\s+", " ", text).strip()
    return text


def simple_tokens(text: str, max_tokens: int = 400) -> list[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", normalize_for_matching(text))
    out: list[str] = []
    seen = set()
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_tokens:
            break
    return out


def process_one_raw(doc: dict) -> dict[str, Any]:
    canonical = doc.get("canonical_url") or ""
    body = doc.get("body_text") or ""
    title = doc.get("title") or ""
    combined = f"{title}\n{body}"
    cleaned = clean_text(combined)
    normalized_text = normalize_for_matching(combined)
    normalized_terms = simple_tokens(combined)
    cred = predict_credibility(cleaned, title=title)
    sum_result = summarize_text(cleaned, title=title)
    summary = sum_result["summary"]
    entities = extract_entities(cleaned, title=title)
    topic_keywords = extract_topic_keywords(cleaned, title, summary, entities)
    published_at = doc.get("published_at")
    now = datetime.now(timezone.utc)

    processed_doc = {
        "canonical_url": canonical,
        "raw_canonical_url": canonical,
        "title": title,
        "source_key": doc.get("source_key"),
        "published_at": published_at,
        "clean_text": cleaned[:50000],
        "normalized_text": normalized_text[:50000],
        "normalized_terms": normalized_terms,
        "summary": summary[:10000],
        "entities": entities,
        "topic_keywords": topic_keywords,
        "processed_at": now,
        "language": "en",
        "model_versions": {
            "fake_detection": cred.get("fake_detection_model_id"),
            "fact_checker": cred.get("fact_check_provider") if cred.get("fact_check_enabled") else "disabled",
            "credibility": cred.get("credibility_model_id"),
            "ner": ner_model_id(),
            "summarizer": sum_result.get("summarizer_model_id"),
            "summarizer_mode": sum_result.get("summarizer_mode"),
        },
        **cred,
    }

    proc = processed_collection()
    proc.replace_one({"canonical_url": canonical}, processed_doc, upsert=True)
    stored = proc.find_one({"canonical_url": canonical}) or processed_doc

    raw_collection().update_one(
        {"_id": doc["_id"]},
        {"$set": {"pipeline_status": "done", "processed_at": now}},
    )

    try:
        notify_keyword_matches_for_article(stored)
    except Exception:
        pass

    return {"ok": True, "canonical_url": canonical}


def claim_next_pending() -> Optional[dict[str, Any]]:
    """Atomically claim one pending raw article for processing."""
    col = raw_collection()
    now = datetime.now(timezone.utc)
    return col.find_one_and_update(
        {"pipeline_status": "pending"},
        {
            "$set": {
                "pipeline_status": "processing",
                "processing_started_at": now,
            }
        },
        sort=[("fetched_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


def requeue_stale_processing(*, stale_minutes: int = 30) -> int:
    """Reset raw articles stuck in processing (e.g. crashed worker) back to pending."""
    col = raw_collection()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, stale_minutes))
    result = col.update_many(
        {
            "pipeline_status": "processing",
            "$or": [
                {"processing_started_at": {"$lt": cutoff}},
                {"processing_started_at": {"$exists": False}},
            ],
        },
        {
            "$set": {"pipeline_status": "pending"},
            "$unset": {"processing_started_at": ""},
        },
    )
    return int(result.modified_count)


def mark_raw_for_reprocess(*, include_failed: bool = True) -> int:
    """
    Queue existing raw_articles for another pipeline pass (same processed_articles upsert).
    Does not create any new MongoDB collection.
    """
    col = raw_collection()
    statuses = ["done"]
    if include_failed:
        statuses.append("failed")
    result = col.update_many(
        {"pipeline_status": {"$in": statuses}},
        {"$set": {"pipeline_status": "pending"}, "$unset": {"pipeline_error": ""}},
    )
    return int(result.modified_count)


def _process_claimed_raw(doc: dict) -> dict[str, Any]:
    """Process one claimed document; mark failed on error."""
    col = raw_collection()
    try:
        return process_one_raw(doc)
    except Exception as e:
        err_msg = str(e)[:500]
        col.update_one(
            {"_id": doc["_id"]},
            {"$set": {"pipeline_status": "failed", "pipeline_error": err_msg}},
        )
        try:
            from notifications.admin_alerts import notify_admin_pipeline_error

            notify_admin_pipeline_error(
                error=err_msg,
                canonical_url=str(doc.get("canonical_url") or ""),
                context="run_batch",
            )
        except Exception:
            pass
        return {"ok": False, "error": str(e), "canonical_url": doc.get("canonical_url")}


def _worker_loop(
    *,
    max_articles: int,
    shared: dict[str, Any],
) -> None:
    """Claim and process articles until limit reached or queue empty."""
    while True:
        with _batch_lock:
            if shared["processed"] + shared["errors"] >= max_articles:
                return
        doc = claim_next_pending()
        if doc is None:
            return
        detail = _process_claimed_raw(doc)
        with _batch_lock:
            shared["details"].append(detail)
            if detail.get("ok"):
                shared["processed"] += 1
            else:
                shared["errors"] += 1


def run_batch(limit: int = 10, *, workers: int = 1) -> dict[str, Any]:
    workers = max(1, min(8, int(workers)))
    limit = max(1, int(limit))

    if workers == 1:
        col = raw_collection()
        pending = list(
            col.find({"pipeline_status": "pending"}).sort("fetched_at", 1).limit(limit)
        )
        ok, errors = 0, 0
        details: list[dict] = []
        for doc in pending:
            col.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "pipeline_status": "processing",
                        "processing_started_at": datetime.now(timezone.utc),
                    }
                },
            )
            detail = _process_claimed_raw(doc)
            details.append(detail)
            if detail.get("ok"):
                ok += 1
            else:
                errors += 1
    else:
        shared: dict[str, Any] = {"processed": 0, "errors": 0, "details": []}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_worker_loop, max_articles=limit, shared=shared)
                for _ in range(workers)
            ]
            for fut in as_completed(futures):
                fut.result()
        ok = shared["processed"]
        errors = shared["errors"]
        details = shared["details"]

    result = {
        "processed_ok": ok,
        "errors": errors,
        "details": details,
        "workers": workers,
    }
    try:
        from notifications.admin_alerts import notify_admin_pipeline_batch

        notify_admin_pipeline_batch(processed_ok=ok, errors=errors)
    except Exception:
        pass
    return result


def run_until_empty(
    *,
    batch_size: int = 50,
    max_articles: int = 0,
    workers: int = 1,
) -> dict[str, Any]:
    """Process all pending raw_articles in batches (upsert into processed_articles only)."""
    total_ok = 0
    total_errors = 0
    batches = 0
    while True:
        if max_articles and total_ok + total_errors >= max_articles:
            break
        limit = batch_size
        if max_articles:
            limit = min(batch_size, max_articles - total_ok - total_errors)
        result = run_batch(limit=limit, workers=workers)
        batches += 1
        total_ok += result["processed_ok"]
        total_errors += result["errors"]
        if result["processed_ok"] == 0 and result["errors"] == 0:
            break
    pending_left = raw_collection().count_documents({"pipeline_status": "pending"})
    return {
        "processed_ok": total_ok,
        "errors": total_errors,
        "batches": batches,
        "pending_remaining": pending_left,
        "workers": workers,
    }
