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

from bson import ObjectId
from pymongo import ReturnDocument

from news.credibility.inference import predict_credibility
from news.article_media import article_image_url
from news.mongo_db import processed_collection, raw_collection
from news.notifications.keyword_alerts import notify_keyword_matches_for_article
from news.pipeline.errors import is_transient_pipeline_error
from news.pipeline.keywords import extract_topic_keywords
from news.pipeline.ner import extract_entities, ner_model_id
from news.pipeline.worker_context import pipeline_worker_active
from news.article_text import sanitize_article_body, sanitize_article_summary
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
    cleaned = sanitize_article_body(clean_text(combined), title=title)
    normalized_text = normalize_for_matching(combined)
    normalized_terms = simple_tokens(combined)
    cred = predict_credibility(cleaned, title=title)
    sum_result = summarize_text(cleaned, title=title)
    summary = sanitize_article_summary(sum_result["summary"], title=title, body=cleaned)
    entities = extract_entities(cleaned, title=title)
    topic_keywords = extract_topic_keywords(cleaned, title, summary, entities)
    published_at = doc.get("published_at")
    now = datetime.now(timezone.utc)
    image_url = article_image_url(doc)

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
    if image_url:
        processed_doc["image_url"] = image_url

    from news.moderation_rules import initial_moderation_status

    processed_doc["moderation_status"] = initial_moderation_status({**processed_doc, **cred})

    proc = processed_collection()
    proc.replace_one({"canonical_url": canonical}, processed_doc, upsert=True)
    stored = proc.find_one({"canonical_url": canonical}) or processed_doc

    raw_collection().update_one(
        {"_id": doc["_id"]},
        {"$set": {"pipeline_status": "done", "processed_at": now}},
    )

    try:
        from news.moderation_rules import article_visible_to_users

        if article_visible_to_users(stored):
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


def requeue_abandoned_processing() -> int:
    """
    After workers finish, any row still in processing was abandoned (reload, crash).
    Put it back on pending so the next batch can claim it.
    """
    col = raw_collection()
    result = col.update_many(
        {"pipeline_status": "processing"},
        {"$set": {"pipeline_status": "pending"}, "$unset": {"processing_started_at": ""}},
    )
    return int(result.modified_count)


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


def requeue_transient_failures() -> int:
    """Move failed raw articles with retryable errors back to pending."""
    col = raw_collection()
    failed = list(
        col.find(
            {"pipeline_status": "failed", "pipeline_error": {"$exists": True, "$ne": ""}},
            {"pipeline_error": 1},
        )
    )
    ids = [doc["_id"] for doc in failed if is_transient_pipeline_error(doc.get("pipeline_error") or "")]
    if not ids:
        return 0
    result = col.update_many(
        {"_id": {"$in": ids}},
        {"$set": {"pipeline_status": "pending"}, "$unset": {"pipeline_error": ""}},
    )
    return int(result.modified_count)


def heal_stuck_raw_pipeline(*, stale_minutes: int = 30) -> dict[str, int]:
    """
    Fix raw rows stuck in processing:
    - already have processed_articles output → mark done (only if not actively processing)
    - otherwise stale → back to pending

    Skips rows with a recent processing_started_at so parallel workers are not cleared
    from the processing count while the dashboard polls analytics.
    """
    raw = raw_collection()
    proc_name = processed_collection().name
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, stale_minutes))
    stale_processing_match = {
        "pipeline_status": "processing",
        "$or": [
            {"processing_started_at": {"$lt": cutoff}},
            {"processing_started_at": {"$exists": False}},
        ],
    }
    healed = 0
    try:
        stuck_ids = [
            doc["_id"]
            for doc in raw.aggregate(
                [
                    {"$match": stale_processing_match},
                    {"$project": {"canonical_url": 1}},
                    {
                        "$lookup": {
                            "from": proc_name,
                            "localField": "canonical_url",
                            "foreignField": "canonical_url",
                            "as": "proc",
                        }
                    },
                    {"$match": {"proc.0": {"$exists": True}}},
                    {"$project": {"_id": 1}},
                ]
            )
        ]
        if stuck_ids:
            result = raw.update_many(
                {"_id": {"$in": stuck_ids}},
                {
                    "$set": {"pipeline_status": "done", "processed_at": datetime.now(timezone.utc)},
                    "$unset": {"processing_started_at": ""},
                },
            )
            healed = int(result.modified_count)
    except Exception:
        healed = 0
    requeued = requeue_stale_processing(stale_minutes=stale_minutes)
    transient = requeue_transient_failures()
    return {"healed_done": healed, "requeued": requeued, "requeued_transient": transient}


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


def requeue_failed_raw_by_id(article_id) -> bool:
    """Move one failed (or stuck processing) raw article back to pending."""
    try:
        oid = ObjectId(str(article_id))
    except Exception:
        return False
    col = raw_collection()
    doc = col.find_one({"_id": oid})
    if not doc:
        return False
    status = str(doc.get("pipeline_status") or "").lower()
    if status not in {"failed", "processing"}:
        return False
    result = col.update_one(
        {"_id": oid, "pipeline_status": doc.get("pipeline_status")},
        {
            "$set": {"pipeline_status": "pending"},
            "$unset": {"pipeline_error": "", "processing_started_at": ""},
        },
    )
    return bool(result.modified_count)


def requeue_all_failed_raw() -> int:
    """Move every failed raw article back to pending for another pipeline pass."""
    col = raw_collection()
    result = col.update_many(
        {"pipeline_status": "failed"},
        {
            "$set": {"pipeline_status": "pending"},
            "$unset": {"pipeline_error": "", "processing_started_at": ""},
        },
    )
    return int(result.modified_count)


def delete_all_failed_raw() -> int:
    """Permanently remove all raw articles stuck in pipeline_status=failed."""
    col = raw_collection()
    result = col.delete_many({"pipeline_status": "failed"})
    return int(result.deleted_count)


def _process_claimed_raw(doc: dict) -> dict[str, Any]:
    """Process one claimed document; mark failed on error."""
    col = raw_collection()
    try:
        return process_one_raw(doc)
    except Exception as e:
        err_msg = str(e)[:500]
        if is_transient_pipeline_error(e):
            col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"pipeline_status": "pending"}, "$unset": {"pipeline_error": ""}},
            )
            return {
                "ok": False,
                "requeued": True,
                "error": err_msg,
                "canonical_url": doc.get("canonical_url"),
            }
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
        return {"ok": False, "error": err_msg, "canonical_url": doc.get("canonical_url")}


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
        token = pipeline_worker_active.set(True)
        try:
            detail = _process_claimed_raw(doc)
        finally:
            pipeline_worker_active.reset(token)
        with _batch_lock:
            if detail.get("requeued"):
                continue
            shared["details"].append(detail)
            if detail.get("ok"):
                shared["processed"] += 1
            else:
                shared["errors"] += 1


def run_batch(limit: int = 10, *, workers: int = 1) -> dict[str, Any]:
    workers = max(1, min(8, int(workers)))
    limit = max(1, int(limit))

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
    """Process pending raw_articles in batches until the pending queue is empty."""
    col = raw_collection()
    total_ok = 0
    total_errors = 0
    batches = 0
    requeued_abandoned = 0
    while True:
        pending_left = col.count_documents({"pipeline_status": "pending"})
        processing_left = col.count_documents({"pipeline_status": "processing"})
        if pending_left == 0:
            if processing_left == 0:
                break
            requeued_abandoned += requeue_abandoned_processing()
            pending_left = col.count_documents({"pipeline_status": "pending"})
            if pending_left == 0:
                break
        if max_articles and total_ok + total_errors >= max_articles:
            break
        limit = min(batch_size, pending_left)
        if max_articles:
            limit = min(limit, max(1, max_articles - total_ok - total_errors))
        result = run_batch(limit=limit, workers=workers)
        batches += 1
        total_ok += result["processed_ok"]
        total_errors += result["errors"]
        if result["processed_ok"] == 0 and result["errors"] == 0:
            requeued_abandoned += requeue_abandoned_processing()
            if col.count_documents({"pipeline_status": "pending"}) == 0:
                break
    pending_left = col.count_documents({"pipeline_status": "pending"})
    processing = col.count_documents({"pipeline_status": "processing"})
    return {
        "processed_ok": total_ok,
        "errors": total_errors,
        "batches": batches,
        "requeued_abandoned": requeued_abandoned,
        "pending_remaining": pending_left,
        "processing": processing,
        "workers": workers,
        "drained": pending_left == 0 and processing == 0,
    }
