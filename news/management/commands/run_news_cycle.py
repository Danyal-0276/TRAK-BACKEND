"""One-shot: scrape -> AI pipeline so articles reach processed_articles and the user feed.

The app feed reads from ``processed_articles`` (see ``news.services.article_query``).
Raw rows must be ``pipeline_status=pending``; scrapers set that on insert.

Examples::

    python manage.py run_news_cycle
    python manage.py run_news_cycle --sources rss dawn --scrape-limit 30 --pipeline-limit 40
    python manage.py run_news_cycle --skip-scrape --pipeline-limit 100
    python manage.py run_news_cycle --skip-pipeline --sources rss --scrape-limit 15

Schedule this command every N minutes via cron, Windows Task Scheduler, or systemd timer.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run scrape_raw_news then run_ai_pipeline (full path to processed articles / feed)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sources",
            nargs="+",
            default=["rss"],
            help="Passed to scrape_raw_news (e.g. rss dawn dunya). Default: rss.",
        )
        parser.add_argument(
            "--scrape-limit",
            type=int,
            default=25,
            help="Max new articles per source for scrape_raw_news (default 25).",
        )
        parser.add_argument(
            "--pipeline-limit",
            type=int,
            default=30,
            help="Max pending raw docs for run_ai_pipeline (default 30).",
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
        if not options["skip_scrape"]:
            self.stdout.write(self.style.NOTICE("=== Scrape -> raw_articles (pending) ==="))
            call_command(
                "scrape_raw_news",
                sources=options["sources"],
                limit=options["scrape_limit"],
            )
        else:
            self.stdout.write(self.style.WARNING("Skipping scrape."))

        if not options["skip_pipeline"]:
            self.stdout.write(self.style.NOTICE("=== AI pipeline -> processed_articles ==="))
            opts = {"limit": options["pipeline_limit"]}
            if options["no_preload_model"]:
                opts["no_preload_model"] = True
            call_command("run_ai_pipeline", **opts)
        else:
            self.stdout.write(self.style.WARNING("Skipping pipeline."))

        self.stdout.write(
            self.style.SUCCESS(
                "Cycle finished. Feed uses processed_articles; clients call GET /api/user/feed/."
            )
        )
