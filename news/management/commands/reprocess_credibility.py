"""Re-run fake detection + credibility merge on processed articles (fixes identical stub scores)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from news.credibility.inference import predict_credibility
from news.mongo_db import processed_collection


class Command(BaseCommand):
    help = (
        "Recompute credibility_* on processed_articles from article text. "
        "If manage.py fails (missing django-mongodb-backend or Python 3.14), run: "
        "python scripts/reprocess_credibility.py"
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0, help="Max docs (0 = all)")
        parser.add_argument(
            "--source-key",
            type=str,
            default="",
            help="Only reprocess articles with this source_key (e.g. dawn_news)",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        limit = int(options["limit"] or 0)
        source_key = (options["source_key"] or "").strip()

        query: dict = {}
        if source_key:
            query["source_key"] = source_key

        coll = processed_collection()
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
        if limit > 0:
            cursor = cursor.limit(limit)

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
                if k.startswith(
                    (
                        "fake_detection_",
                        "fact_check_",
                        "credibility_",
                    )
                )
            }
            if not fields:
                continue

            if dry_run:
                self.stdout.write(
                    f"  {title[:50]!r} → label={fields.get('credibility_label')} "
                    f"score={fields.get('credibility_score')} "
                    f"probs={fields.get('credibility_probs')}"
                )
                updated += 1
                continue

            coll.update_one({"_id": doc["_id"]}, {"$set": fields})
            updated += 1

        verb = "Would update" if dry_run else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} {updated} article(s)."))
