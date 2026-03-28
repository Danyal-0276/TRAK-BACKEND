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
            help="dawn | dunya | rss | generic_sites (see news/scrapers/sources_catalog.py).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Max new articles to store per source (default 25).",
        )

    def handle(self, *args, **options):
        storage.ensure_indexes()
        client = PoliteHttpClient()
        try:
            names = options["sources"]
            limit = max(1, options["limit"])

            self.stdout.write(
                "Using robots.txt checks + delay between requests. "
                "Set SCRAPER_USER_AGENT to a reachable contact if you deploy."
            )

            for name in names:
                mod = SOURCE_MODULES[name]
                stats = mod.run(client, limit=limit)
                self.stdout.write(self.style.SUCCESS(str(stats)))
        finally:
            client.close()
