"""Recompute credibility_score and align probs from stored label + max_prob."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from news.credibility.score import (
    compute_credibility_score_from_doc,
    effective_credibility_probs,
)
from news.mongo_db import processed_collection


class Command(BaseCommand):
    help = "Backfill credibility_score and fix template credibility_probs on processed_articles."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print counts only, do not write.")
        parser.add_argument("--limit", type=int, default=0, help="Max documents to update (0 = all).")

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        limit = int(options["limit"] or 0)
        coll = processed_collection()
        cursor = coll.find(
            {"credibility_label": {"$exists": True}},
            projection={
                "credibility_label": 1,
                "credibility_probs": 1,
                "credibility_max_prob": 1,
                "fake_detection_max_prob": 1,
            },
        )
        if limit > 0:
            cursor = cursor.limit(limit)

        updated = 0
        for doc in cursor:
            eff = effective_credibility_probs(doc)
            score = compute_credibility_score_from_doc(doc)
            if eff is None and score is None:
                continue
            payload: dict = {}
            if eff is not None:
                payload["credibility_probs"] = eff
            if score is not None:
                payload["credibility_score"] = score
            if not payload:
                continue
            if dry_run:
                updated += 1
                continue
            coll.update_one({"_id": doc["_id"]}, {"$set": payload})
            updated += 1

        verb = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} {updated} processed article(s)."))
