"""
Process raw_articles with pipeline_status=pending → processed_articles + done/failed.
Stages: clean text → credibility → lightweight extractive summary stub → NER stub.
"""

from __future__ import annotations

import re
import string
from datetime import datetime, timezone
from typing import Any

from news.credibility.inference import predict_credibility
from news.mongo_db import processed_collection, raw_collection
from news.pipeline.keywords import extract_topic_keywords


def clean_text(text: str) -> str:
    text = text or ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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


def extractive_summary(text: str, max_sentences: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:max_sentences]) if parts else text[:400]


def stub_ner(text: str) -> list[dict[str, Any]]:
    """Placeholder entities (replace with real NER later)."""
    caps = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text[:2000])
    seen = set()
    out = []
    for w in caps[:12]:
        if w not in seen and len(w) > 2:
            seen.add(w)
            out.append({"text": w, "label": "MISC"})
    return out


def process_one_raw(doc: dict) -> dict[str, Any]:
    canonical = doc.get("canonical_url") or ""
    body = doc.get("body_text") or ""
    title = doc.get("title") or ""
    combined = f"{title}\n{body}"
    cleaned = clean_text(combined)
    normalized_text = normalize_for_matching(combined)
    normalized_terms = simple_tokens(combined)
    cred = predict_credibility(cleaned)
    summary = extractive_summary(cleaned)
    entities = stub_ner(cleaned)
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
        "model_versions": {"credibility": cred.get("credibility_model_id"), "ner": "stub-1"},
        **cred,
    }

    proc = processed_collection()
    proc.replace_one({"canonical_url": canonical}, processed_doc, upsert=True)

    raw_collection().update_one(
        {"_id": doc["_id"]},
        {"$set": {"pipeline_status": "done", "processed_at": now}},
    )

    return {"ok": True, "canonical_url": canonical}


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
