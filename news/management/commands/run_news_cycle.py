"""One-shot: scrape -> AI pipeline so articles reach processed_articles and the user feed.

The app feed reads from ``processed_articles`` (see ``news.services.article_query``).
Raw rows must be ``pipeline_status=pending``; scrapers set that on insert.

Examples::

    python manage.py run_news_cycle
    python manage.py run_news_cycle --sources dawn dunya rss generic_sites --scrape-limit 45 --pipeline-all --workers 3
    python manage.py run_news_cycle --skip-scrape --pipeline-limit 100 --workers 3
    python manage.py run_news_cycle --skip-pipeline --sources rss --scrape-limit 15

Schedule via Render Cron, Windows Task Scheduler, or VPS systemd timer (see deploy/vps-systemd.md).
"""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run scrape_raw_news then run_ai_pipeline (full path to processed articles / feed)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sources",
            nargs="+",
            default=[
                "currents",
                "newsdata",
                "gnews",
                "rss",
                "generic_sites",
                "dunya",
                "dawn",
            ],
            help="Passed to scrape_raw_news. APIs and RSS run before built-in Pakistani sites.",
        )
        parser.add_argument(
            "--scrape-limit",
            type=int,
            default=40,
            help="Max new articles per source for scrape_raw_news (default 40).",
        )
        parser.add_argument(
            "--total-limit",
            type=int,
            default=None,
            help="Max new inserts across all sources (optional; used by admin scrape).",
        )
        parser.add_argument(
            "--pipeline-limit",
            type=int,
            default=200,
            help="Max pending raw docs for run_ai_pipeline when not using --pipeline-all (default 200).",
        )
        parser.set_defaults(pipeline_all=True)
        parser.add_argument(
            "--no-pipeline-all",
            action="store_false",
            dest="pipeline_all",
            help="Only process up to --pipeline-limit instead of draining the queue.",
        )
        parser.add_argument(
            "--pipeline-batch-size",
            type=int,
            default=50,
            help="Batch size when using --pipeline-all (default 50).",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=None,
            help="Parallel pipeline workers (default: PIPELINE_WORKERS env or 1).",
        )
        parser.add_argument(
            "--requeue-stale",
            action="store_true",
            help="Reset stuck processing rows before pipeline (forwarded to run_ai_pipeline).",
        )
        parser.add_argument(
            "--skip-scrape",
            action="store_true",
            help="Only run the AI pipeline (process existing pending raw).",
        )
        parser.add_argument(
            "--skip-pipeline",
            action="store_true",
            help="Only run scrapers (leave processing for later).",
        )
        parser.add_argument(
            "--no-preload-model",
            action="store_true",
            help="Forward to run_ai_pipeline (faster startup if model loads lazily).",
        )

    def handle(self, *args, **options):
        from news.pipeline.auto_runner import (
            release_pipeline_cycle_lock,
            try_acquire_pipeline_cycle_lock,
        )

        cycle_lock = try_acquire_pipeline_cycle_lock()
        try:
            self._run_cycle(options)
        finally:
            if cycle_lock:
                release_pipeline_cycle_lock()

    def _run_cycle(self, options):
        if not options["skip_scrape"]:
            self.stdout.write(self.style.NOTICE("=== Scrape -> raw_articles (pending) ==="))
            scrape_kwargs = {
                "sources": options["sources"],
                "limit": options["scrape_limit"],
            }
            if options.get("total_limit") is not None:
                scrape_kwargs["total_limit"] = options["total_limit"]
            call_command("scrape_raw_news", **scrape_kwargs)
        else:
            self.stdout.write(self.style.WARNING("Skipping scrape."))

        if not options["skip_pipeline"]:
            self.stdout.write(self.style.NOTICE("=== AI pipeline -> processed_articles ==="))
            opts: dict = {}
            if options["pipeline_all"]:
                opts["all"] = True
                opts["batch_size"] = max(1, options["pipeline_batch_size"])
            else:
                opts["limit"] = options["pipeline_limit"]
            if options["no_preload_model"]:
                opts["no_preload_model"] = True
            if options["requeue_stale"]:
                opts["requeue_stale"] = True
            workers = options["workers"]
            if workers is None:
                workers = getattr(settings, "PIPELINE_WORKERS", 1)
            opts["workers"] = workers
            call_command("run_ai_pipeline", **opts)
        else:
            self.stdout.write(self.style.WARNING("Skipping pipeline."))

        self.stdout.write(
            self.style.SUCCESS(
                "Cycle finished. Feed uses processed_articles; clients call GET /api/user/feed/."
            )
        )

