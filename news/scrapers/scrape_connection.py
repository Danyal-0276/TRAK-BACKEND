"""Scrape a single admin connection (one of the ~38 sources in Manage Connection)."""

from __future__ import annotations

from django.conf import settings

from news.scrapers.client import PoliteHttpClient
from news.scrapers.sources import SOURCE_MODULES
from news.scrapers.sources.generic_sites import find_site_config, run_single_site


def scrape_connection_target(
    client: PoliteHttpClient,
    target: dict,
    *,
    limit: int,
) -> dict:
    """Run the scraper for one admin connection with its fair-share insert cap."""
    kind = str(target.get("kind") or "rss").strip().lower()
    cap = max(1, int(limit))

    if kind == "rss":
        from news.scrapers.sources.rss import run_single_feed

        return run_single_feed(client, feed_url=str(target.get("url") or ""), limit=cap)

    if kind == "generic_site":
        cfg = find_site_config(
            source_key=target.get("source_key"),
            listing_url=target.get("url"),
        )
        if not cfg:
            return {
                "inserted": 0,
                "skipped": 0,
                "source": "generic_sites",
                "target": target.get("name"),
                "note": "site config not found",
            }
        return run_single_site(client, cfg=cfg, limit=cap)

    if kind in ("currents", "newsdata", "gnews"):
        key_name = f"{kind.upper()}_API_KEY"
        if not (getattr(settings, key_name, "") or "").strip():
            return {
                "inserted": 0,
                "skipped": 0,
                "source": kind,
                "target": target.get("name"),
                "note": "API key not configured",
            }
        return SOURCE_MODULES[kind].run(client, limit=cap)

    if kind == "builtin":
        mod_name = str(target.get("scraper_module") or "").strip()
        if mod_name not in SOURCE_MODULES:
            return {
                "inserted": 0,
                "skipped": 0,
                "source": mod_name or "builtin",
                "target": target.get("name"),
                "note": "unknown builtin scraper",
            }
        stats = SOURCE_MODULES[mod_name].run(client, limit=cap)
        stats["target"] = target.get("name")
        return stats

    return {
        "inserted": 0,
        "skipped": 0,
        "source": kind,
        "target": target.get("name"),
        "note": f"unsupported connection kind: {kind}",
    }
