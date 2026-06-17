"""
Ingest news articles into MongoDB (`raw_articles`): title, body text, dates, author, etc.

Respects robots.txt (blocks fetches when disallowed or robots cannot be loaded),
uses configurable delay between requests, and deduplicates by canonical URL.
Set SCRAPER_STORE_RAW_HTML=true only if you also need full HTML.

Examples:
  python manage.py scrape_raw_news --sources dawn dunya
  python manage.py scrape_raw_news --sources rss --limit 10
  python manage.py scrape_raw_news --total-limit 150
"""

from django.core.management.base import BaseCommand

from news.scrape_sources import fair_caps_for_ids, list_active_scrape_targets
from news.scrapers.client import PoliteHttpClient
from news.scrapers import storage
from news.scrapers.scrape_connection import scrape_connection_target
from news.scrapers.sources import SOURCE_MODULES

_last_scrape_insert_count = 0


def get_last_scrape_insert_count() -> int:
    """New inserts from the most recent scrape_raw_news run (0 if none yet)."""
    return _last_scrape_insert_count


def _fair_per_source_caps(source_names: list[str], per_source_limit: int, total_limit: int | None) -> dict[str, int]:
    """Split a global insert cap evenly so the first source cannot take the whole batch."""
    if total_limit is None:
        return {name: per_source_limit for name in source_names}
    return fair_caps_for_ids(source_names, total_limit, per_id_max=per_source_limit)


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
                "When set, the cap is split evenly across active admin connections "
                "(Manage Connection), not only scraper modules."
            ),
        )

    def _run_connection_fair(
        self,
        client: PoliteHttpClient,
        *,
        targets: list[dict],
        total_limit: int,
        per_target_max: int,
    ) -> int:
        hard_cap = max(1, int(total_limit))
        caps = fair_caps_for_ids(
            [str(t["id"]) for t in targets],
            hard_cap,
            per_id_max=per_target_max,
        )
        shares = ", ".join(f"{t['name']}={caps.get(t['id'], 0)}" for t in targets[:8])
        if len(targets) > 8:
            shares = f"{shares}, … (+{len(targets) - 8} more)"
        self.stdout.write(
            self.style.NOTICE(
                f"Total insert cap: {hard_cap} split across {len(targets)} admin connection(s) ({shares})."
            )
        )

        total_inserted = 0
        for target in targets:
            cap = int(caps.get(target["id"], 0) or 0)
            if cap <= 0:
                continue
            remaining = hard_cap - total_inserted
            if remaining <= 0:
                self.stdout.write(
                    self.style.WARNING(
                        f"Total insert cap ({hard_cap}) reached; skipping remaining connections."
                    )
                )
                break
            effective = min(cap, remaining)
            stats = scrape_connection_target(client, target, limit=effective)
            inserted = int(stats.get("inserted") or 0)
            total_inserted += inserted
            label = target.get("name") or target.get("id")
            self.stdout.write(self.style.SUCCESS(f"{label}: {stats}"))
            self.stdout.write(
                self.style.NOTICE(f"Run total new inserts: {total_inserted}/{hard_cap}")
            )
        return total_inserted

    def _run_module_sources(
        self,
        client: PoliteHttpClient,
        *,
        names: list[str],
        limit: int,
        total_limit: int | None,
    ) -> int:
        per_source_caps = _fair_per_source_caps(names, limit, total_limit)

        if total_limit is not None:
            shares = ", ".join(f"{n}={per_source_caps[n]}" for n in names)
            self.stdout.write(
                self.style.NOTICE(
                    f"Total insert cap: {int(total_limit)} split across {len(names)} scraper module(s) ({shares})."
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
        return total_inserted

    def handle(self, *args, **options):
        global _last_scrape_insert_count
        _last_scrape_insert_count = 0

        storage.ensure_indexes()
        client = PoliteHttpClient()
        try:
            limit = max(1, options["limit"])
            total_limit = options.get("total_limit")

            self.stdout.write(
                "Using robots.txt checks + delay between requests. "
                "Set SCRAPER_USER_AGENT to a reachable contact if you deploy."
            )

            if total_limit is not None:
                targets = list_active_scrape_targets()
                if targets:
                    total_inserted = self._run_connection_fair(
                        client,
                        targets=targets,
                        total_limit=int(total_limit),
                        per_target_max=limit,
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            "No active admin connections found; falling back to scraper modules."
                        )
                    )
                    total_inserted = self._run_module_sources(
                        client,
                        names=options["sources"],
                        limit=limit,
                        total_limit=total_limit,
                    )
            else:
                total_inserted = self._run_module_sources(
                    client,
                    names=options["sources"],
                    limit=limit,
                    total_limit=None,
                )

            _last_scrape_insert_count = total_inserted
        finally:
            client.close()

        from news.pipeline.auto_runner import schedule_immediate_drain

        schedule_immediate_drain()
