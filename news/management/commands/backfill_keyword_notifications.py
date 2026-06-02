"""Drain pipeline backlog and send keyword alerts for recent approved articles."""

from django.core.management.base import BaseCommand
from django.conf import settings

from news.notifications.keyword_alerts import notify_keyword_matches_for_recent_articles
from news.pipeline import orchestrator
from news.pipeline.auto_runner import clear_stale_auto_lock


class Command(BaseCommand):
    help = "Heal pipeline queue, process pending raw articles, backfill keyword notifications."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=168, help="Look back N hours (default 168).")
        parser.add_argument("--limit", type=int, default=200, help="Max articles to scan (default 200).")
        parser.add_argument("--skip-pipeline", action="store_true", help="Only backfill notifications.")

    def handle(self, *args, **options):
        hours = max(1, int(options["hours"]))
        limit = max(1, min(int(options["limit"]), 500))
        skip_pipeline = bool(options["skip_pipeline"])

        if not skip_pipeline:
            clear_stale_auto_lock()
            heal = orchestrator.heal_stuck_raw_pipeline(
                stale_minutes=getattr(settings, "PIPELINE_STALE_MINUTES", 30)
            )
            self.stdout.write(f"Pipeline heal: {heal}")
            workers = max(1, min(8, int(getattr(settings, "PIPELINE_WORKERS", 1))))
            batch = max(1, min(500, int(getattr(settings, "PIPELINE_AUTO_BATCH_SIZE", 50))))
            result = orchestrator.run_until_empty(batch_size=batch, workers=workers)
            self.stdout.write(
                f"Pipeline drain: ok={result.get('processed_ok')} "
                f"errors={result.get('errors')} pending={result.get('pending_remaining')} "
                f"processing={result.get('processing')} drained={result.get('drained')}"
            )

        sent = notify_keyword_matches_for_recent_articles(hours=hours, limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Keyword notifications sent (or deduped): {sent}"))
