"""Backfill processed_articles moderation_status and image_url from rules + raw rows."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from news.article_media import article_image_url, hydrate_processed_image_urls
from news.moderation_rules import initial_moderation_status
from news.mongo_db import processed_collection, raw_collection


class Command(BaseCommand):
    help = "Set moderation_status and image_url on processed_articles from pipeline rules and raw_articles."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0, help="Max docs (0 = all)")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        limit = int(options["limit"] or 0)

        proc_col = processed_collection()
        raw_col = raw_collection()
        cursor = proc_col.find({})
        if limit > 0:
            cursor = cursor.limit(limit)

        updated = 0
        for doc in cursor:
            hydrate_processed_image_urls([doc], raw_col)
            img = article_image_url(doc)
            mod = initial_moderation_status(doc)
            changes = {}
            if img and doc.get("image_url") != img:
                changes["image_url"] = img
            if doc.get("moderation_status") != mod:
                changes["moderation_status"] = mod
            if not changes:
                continue
            updated += 1
            if dry_run:
                self.stdout.write(f"Would update {doc.get('canonical_url')}: {changes}")
            else:
                proc_col.update_one({"_id": doc["_id"]}, {"$set": changes})

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Would update' if dry_run else 'Updated'} {updated} processed article(s)."
            )
        )
