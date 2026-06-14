"""Backfill ML category labels and keyword embeddings on processed_articles."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from news.categorization.enrich import enrich_article_ml_fields
from news.mongo_db import processed_collection


class Command(BaseCommand):
    help = (
        "Add zero-shot category labels and semantic embeddings to processed articles. "
        "Use --all for full backfill or --limit N for a batch."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100, help="Max articles to update (default 100).")
        parser.add_argument("--all", action="store_true", help="Process all articles missing ML fields.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Recompute even when primary_category and match_embedding already exist.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count matching documents without writing updates.",
        )

    def handle(self, *args, **options):
        limit = max(1, int(options["limit"] or 100))
        force = bool(options["force"])
        dry_run = bool(options["dry_run"])
        proc = processed_collection()

        query: dict = {}
        if not force:
            query = {
                "$or": [
                    {"primary_category": {"$exists": False}},
                    {"primary_category": ""},
                    {"match_embedding": {"$exists": False}},
                    {"match_embedding": []},
                ]
            }

        total = proc.count_documents(query) if query else proc.count_documents({})
        if options["all"]:
            batch_limit = total
        else:
            batch_limit = min(limit, total)

        self.stdout.write(f"Articles to process: {batch_limit} (matching total={total})")
        if dry_run:
            return

        cursor = proc.find(query or {}, {"title": 1, "summary": 1, "clean_text": 1}).limit(batch_limit)
        updated = 0
        for doc in cursor:
            title = str(doc.get("title") or "")
            summary = str(doc.get("summary") or "")
            clean_text = str(doc.get("clean_text") or "")
            fields = enrich_article_ml_fields(title=title, summary=summary, clean_text=clean_text)
            if not fields:
                continue
            proc.update_one({"_id": doc["_id"]}, {"$set": fields})
            updated += 1
            if updated % 10 == 0:
                self.stdout.write(f"  updated {updated}/{batch_limit}...")

        self.stdout.write(self.style.SUCCESS(f"Category backfill complete: updated={updated}"))
