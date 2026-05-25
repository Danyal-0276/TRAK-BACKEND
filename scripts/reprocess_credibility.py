#!/usr/bin/env python3
"""
Re-score processed articles without full Django / django-mongodb-backend.

Usage (from TRAK-BACKEND repo root):
  python scripts/reprocess_credibility.py
  python scripts/reprocess_credibility.py --dry-run
  python scripts/reprocess_credibility.py --limit 10
  python scripts/reprocess_credibility.py --source-key dawn_news

Requires: pymongo, python-dotenv (pip install pymongo python-dotenv)
Optional: gradio-client for HF Space fake detection (FAKE_DETECTION_SPACE_ID in .env)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore

if load_dotenv:
    load_dotenv(ROOT / ".env")


def _configure_minimal_settings() -> None:
    """Configure Django settings object only (no django.setup — breaks on Python 3.14)."""
    from django.conf import settings

    if settings.configured:
        return
    settings.configure(
        CREDIBILITY_CONFIDENCE_THRESHOLD=float(
            os.environ.get("CREDIBILITY_CONFIDENCE_THRESHOLD", "0.6")
        ),
        FAKE_DETECTION_SPACE_ID=os.environ.get("FAKE_DETECTION_SPACE_ID", "").strip() or None,
        FAKE_DETECTION_SPACE_API_NAME=os.environ.get("FAKE_DETECTION_SPACE_API_NAME", "").strip()
        or None,
        HF_TOKEN=os.environ.get("HF_TOKEN", "").strip() or None,
        CREDIBILITY_MODEL_PATH=os.environ.get("CREDIBILITY_MODEL_PATH", "").strip() or None,
        FACT_CHECKER_ENABLED=str(os.environ.get("FACT_CHECKER_ENABLED", "false")).lower()
        in ("1", "true", "yes", "on"),
        FACT_CHECKER_PROVIDERS=os.environ.get(
            "FACT_CHECKER_PROVIDERS", "wikipedia,wikidata,openalex"
        ),
        FACT_CHECKER_PROVIDER=os.environ.get("FACT_CHECKER_PROVIDER", "wikipedia"),
        OPENALEX_MAILTO=os.environ.get("OPENALEX_MAILTO", ""),
        GOOGLE_FACT_CHECK_API_KEY=os.environ.get("GOOGLE_FACT_CHECK_API_KEY", ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Reprocess credibility on processed_articles")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--source-key", type=str, default="")
    args = parser.parse_args()

    _configure_minimal_settings()

    from pymongo import MongoClient

    from news.credibility.inference import predict_credibility

    uri = (
        os.environ.get("MONGODB_URI_DIRECT", "").strip()
        or os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017").strip()
    )
    db_name = os.environ.get("MONGODB_RAW_DATABASE", "TRAK_DB")
    coll_name = os.environ.get("MONGODB_PROCESSED_COLLECTION", "processed_articles")

    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    coll = client[db_name][coll_name]

    query: dict = {}
    if args.source_key.strip():
        query["source_key"] = args.source_key.strip()

    cursor = coll.find(
        query,
        projection={
            "title": 1,
            "clean_text": 1,
            "body_text": 1,
            "content": 1,
            "article_text": 1,
            "normalized_text": 1,
        },
    )
    if args.limit > 0:
        cursor = cursor.limit(args.limit)

    updated = 0
    for doc in cursor:
        title = doc.get("title") or ""
        body = (
            doc.get("clean_text")
            or doc.get("body_text")
            or doc.get("content")
            or doc.get("article_text")
            or doc.get("normalized_text")
            or ""
        )
        text = f"{title}\n{body}".strip()
        if not text:
            continue

        cred = predict_credibility(text, title=title)
        fields = {
            k: cred[k]
            for k in cred
            if k.startswith(("fake_detection_", "fact_check_", "credibility_"))
        }
        if not fields:
            continue

        if args.dry_run:
            print(
                f"  {title[:55]!r} -> label={fields.get('credibility_label')} "
                f"score={fields.get('credibility_score')} "
                f"model={fields.get('fake_detection_model_id')}"
            )
        else:
            coll.update_one({"_id": doc["_id"]}, {"$set": fields})

        updated += 1

    verb = "Would update" if args.dry_run else "Updated"
    print(f"{verb} {updated} article(s) in {db_name}.{coll_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
