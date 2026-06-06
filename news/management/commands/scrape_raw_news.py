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

_last_scrape_insert_count = 0


def get_last_scrape_insert_count() -> int:
    """New inserts from the most recent scrape_raw_news run (0 if none yet)."""
    return _last_scrape_insert_count


def _fair_per_source_caps(source_names: list[str], per_source_limit: int, total_limit: int | None) -> dict[str, int]:
    """Split a global insert cap evenly so the first source cannot take the whole batch."""
    if total_limit is None:
        return {name: per_source_limit for name in source_names}
    n = len(source_names)
    if n == 0:
        return {}
    total = max(1, int(total_limit))
    base, extra = divmod(total, n)
    caps: dict[str, int] = {}
    for i, name in enumerate(source_names):
        share = base + (1 if i < extra else 0)
        caps[name] = min(per_source_limit, max(1, share)) if share > 0 else 0
    return caps


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
            help=(
                "Cap total new inserts across all sources (optional). "
                "The cap is split evenly per source so one outlet cannot consume the whole run."
            ),
        )

    def handle(self, *args, **options):
        global _last_scrape_insert_count
        _last_scrape_insert_count = 0

        storage.ensure_indexes()
        client = PoliteHttpClient()
        try:
            names = options["sources"]
            limit = max(1, options["limit"])
            total_limit = options.get("total_limit")
            per_source_caps = _fair_per_source_caps(names, limit, total_limit)

            self.stdout.write(
                "Using robots.txt checks + delay between requests. "
                "Set SCRAPER_USER_AGENT to a reachable contact if you deploy."
            )
            if total_limit is not None:
                shares = ", ".join(f"{n}={per_source_caps[n]}" for n in names)
                self.stdout.write(
                    self.style.NOTICE(
                        f"Total insert cap: {int(total_limit)} split across {len(names)} source(s) ({shares})."
                    )
                )

            total_inserted = 0
            hard_cap = max(1, int(total_limit)) if total_limit is not None else None

            for name in names:
                per_source = per_source_caps.get(name, limit)
                if per_source <= 0:
                    continue
                if hard_cap is not None:
                    remaining = hard_cap - total_inserted
                    if remaining <= 0:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Total insert cap ({hard_cap}) reached; skipping remaining sources."
                            )
                        )
                        break
                    effective = min(per_source, remaining)
                else:
                    effective = per_source

                mod = SOURCE_MODULES[name]
                run_kwargs: dict = {"limit": effective}
                if name == "generic_sites" and hard_cap is not None:
                    run_kwargs["max_total"] = effective
                stats = mod.run(client, **run_kwargs)
                inserted = int(stats.get("inserted") or 0)
                total_inserted += inserted
                self.stdout.write(self.style.SUCCESS(str(stats)))
                if hard_cap is not None:
                    self.stdout.write(
                        self.style.NOTICE(f"Run total new inserts: {total_inserted}/{hard_cap}")
                    )

            _last_scrape_insert_count = total_inserted
        finally:
            client.close()

        from news.pipeline.auto_runner import schedule_immediate_drain

        schedule_immediate_drain()
