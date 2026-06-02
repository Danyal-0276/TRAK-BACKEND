"""
Ingest news articles into MongoDB (`raw_articles`): title, body text, dates, author, etc.

Respects robots.txt (blocks fetches when disallowed or robots cannot be loaded),
uses configurable delay between requests, and deduplicates by canonical URL.
Set SCRAPER_STORE_RAW_HTML=true only if you also need full HTML.

Examples:
  python manage.py scrape_raw_news --sources dawn dunya
  python manage.py scrape_raw_news --sources rss --limit 10
"""

from django.core.management.base import BaseCommand

from news.scrapers.client import PoliteHttpClient
from news.scrapers import storage
from news.scrapers.sources import SOURCE_MODULES


class Command(BaseCommand):
    help = "Scrape structured articles (title, body, dates, …) from configured sources into MongoDB."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sources",
            nargs="+",
            choices=list(SOURCE_MODULES.keys()),
            default=["dawn", "dunya"],
            help="dawn | dunya | rss | generic_sites | currents | newsdata | gnews.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Max new articles to store per source (default 25).",
        )
        parser.add_argument(
            "--total-limit",
            type=int,
            default=None,
            help="Stop after this many new inserts across all sources (optional).",
        )

    def handle(self, *args, **options):
        storage.ensure_indexes()
        client = PoliteHttpClient()
        try:
            names = options["sources"]
            limit = max(1, options["limit"])
            total_limit = options.get("total_limit")
            remaining = int(total_limit) if total_limit is not None else None

            self.stdout.write(
                "Using robots.txt checks + delay between requests. "
                "Set SCRAPER_USER_AGENT to a reachable contact if you deploy."
            )
            if remaining is not None:
                self.stdout.write(
                    self.style.NOTICE(f"Total insert cap: {remaining} across {len(names)} source(s).")
                )

            for name in names:
                if remaining is not None and remaining <= 0:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Total limit reached; skipping remaining sources ({name} and later)."
                        )
                    )
                    break
                mod = SOURCE_MODULES[name]
                per_source = min(limit, max(1, remaining)) if remaining is not None else limit
                stats = mod.run(client, limit=per_source)
                inserted = int(stats.get("inserted") or 0)
                if remaining is not None:
                    remaining -= inserted
                self.stdout.write(self.style.SUCCESS(str(stats)))
        finally:
            client.close()

        from news.pipeline.auto_runner import schedule_immediate_drain

        schedule_immediate_drain()
