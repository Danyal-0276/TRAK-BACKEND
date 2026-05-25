"""
Process raw_articles with pipeline_status=pending → processed_articles + done/failed.
Stages: clean text → credibility → BART summary (HF) → NER (names/places/orgs).
"""

from __future__ import annotations

import re
import string
from datetime import datetime, timezone
from typing import Any

from news.credibility.inference import predict_credibility
from news.mongo_db import processed_collection, raw_collection
from news.notifications.keyword_alerts import notify_keyword_matches_for_article
from news.pipeline.keywords import extract_topic_keywords
from news.pipeline.ner import extract_entities, ner_model_id
from news.summarization.inference import summarize_text


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


def run_batch(limit: int = 10) -> dict[str, Any]:
    col = raw_collection()
    pending = list(
        col.find({"pipeline_status": "pending"}).sort("fetched_at", 1).limit(limit)
    )
    ok, errors = 0, 0
    details: list[dict] = []
    for doc in pending:
        try:
            col.update_one({"_id": doc["_id"]}, {"$set": {"pipeline_status": "processing"}})
            r = process_one_raw(doc)
            ok += 1
            details.append(r)
        except Exception as e:
            errors += 1
            col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"pipeline_status": "failed", "pipeline_error": str(e)[:500]}},
            )
            details.append({"ok": False, "error": str(e), "canonical_url": doc.get("canonical_url")})
    return {"processed_ok": ok, "errors": errors, "details": details}


def run_until_empty(*, batch_size: int = 50, max_articles: int = 0) -> dict[str, Any]:
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
        result = run_batch(limit=limit)
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
    }
