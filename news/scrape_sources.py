"""Scrape source URLs from admin connections (MongoDB) with catalog fallback."""

from __future__ import annotations

from typing import Any

from urllib.parse import urlparse

from django.conf import settings

from news.platform_taxonomy import list_connections
from news.scrapers.sources_catalog import (
    DAWN_LISTING_URLS,
    DUNYA_LISTING_URLS,
    GENERIC_SITES,
    RSS_FEED_LABELS,
    RSS_FEED_URLS,
)


def _catalog_rss_urls() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in RSS_FEED_URLS + list(getattr(settings, "SCRAPER_RSS_FEED_URLS", []) or []):
        u = (u or "").strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _is_rss_connection(conn: dict) -> bool:
    kind = (conn.get("kind") or "rss").strip().lower()
    if kind in ("builtin", "generic_site"):
        return False
    url = (conn.get("url") or "").strip()
    if not url:
        return False
    if kind == "rss":
        return True
    lower = url.lower()
    return any(token in lower for token in ("/rss", "/feed", ".xml", "rss.xml", "atom"))


def list_rss_feed_urls(*, fallback_to_catalog: bool = True) -> list[str]:
    """
    Active RSS feed URLs from admin connections.
    Falls back to sources_catalog when no RSS connections are configured.
    """
    seen: set[str] = set()
    out: list[str] = []
    for conn in list_connections():
        if not conn.get("active", True):
            continue
        if not _is_rss_connection(conn):
            continue
        url = (conn.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    if fallback_to_catalog:
        for url in _catalog_rss_urls():
            if url not in seen:
                seen.add(url)
                out.append(url)
    return out


def ingest_sources_summary() -> dict[str, Any]:
    """
    Counts for admin dashboard: MongoDB connections vs what scrapers actually use.
    RSS scraper always merges catalog feeds even when Admin lists few connections.
    """
    connections = list_connections()
    active = [c for c in connections if c.get("active", True)]
    by_kind: dict[str, int] = {}
    for c in connections:
        kind = str(c.get("kind") or "rss").strip().lower() or "rss"
        by_kind[kind] = by_kind.get(kind, 0) + 1

    rss_at_scrape = list_rss_feed_urls(fallback_to_catalog=True)
    catalog_rss = _catalog_rss_urls()
    enabled_generic = sum(1 for s in GENERIC_SITES if s.get("enabled"))

    api_keys = {
        "currents": bool(getattr(settings, "CURRENTS_API_KEY", "")),
        "newsdata": bool(getattr(settings, "NEWSDATA_API_KEY", "")),
        "gnews": bool(getattr(settings, "GNEWS_API_KEY", "")),
    }

    return {
        "connections_total": len(connections),
        "connections_active": len(active),
        "rss_feeds_used_by_scraper": len(rss_at_scrape),
        "rss_catalog_feeds": len(catalog_rss),
        "builtin_scrapers": ["dawn", "dunya"],
        "generic_sites_enabled": enabled_generic,
        "api_sources_configured": [k for k, on in api_keys.items() if on],
        "by_kind": by_kind,
    }


def _display_name_for_feed_url(url: str) -> str:
    label = RSS_FEED_LABELS.get((url or "").strip())
    if label:
        return label
    try:
        host = urlparse(url).netloc.replace("www.", "")
        if not host:
            return "RSS feed"
        parts = [p for p in host.split(".") if p]
        # feeds.bbci.co.uk → BBC, not "Feeds"
        if parts and parts[0] in ("feeds", "rss", "www", "m") and len(parts) > 1:
            label = parts[1]
        else:
            label = parts[0] if parts else host
        return label.replace("-", " ").title()
    except Exception:
        return "RSS feed"


def fair_caps_for_ids(
    ids: list[str],
    total_limit: int,
    *,
    per_id_max: int | None = None,
) -> dict[str, int]:
    """Split a global insert cap evenly across connection/source ids."""
    n = len(ids)
    if n == 0:
        return {}
    total = max(1, int(total_limit))
    base, extra = divmod(total, n)
    caps: dict[str, int] = {}
    for i, target_id in enumerate(ids):
        share = base + (1 if i < extra else 0)
        if share <= 0:
            caps[target_id] = 0
            continue
        share = max(1, share)
        if per_id_max is not None:
            share = min(int(per_id_max), share)
        caps[target_id] = share
    return caps


def _api_key_configured(kind: str) -> bool:
    key_name = f"{kind.upper()}_API_KEY"
    return bool((getattr(settings, key_name, "") or "").strip())


def _generic_site_scrapeable(conn: dict[str, Any]) -> bool:
    from news.scrapers.sources.generic_sites import find_site_config

    cfg = find_site_config(
        source_key=conn.get("source_key"),
        listing_url=conn.get("url"),
    )
    return bool(cfg and cfg.get("enabled", True))


def connection_to_scrape_target(conn: dict[str, Any]) -> dict[str, Any] | None:
    """Map one admin connection row to a scrape target, or None if not scrapeable."""
    slug = str(conn.get("slug") or conn.get("id") or "").strip()
    if not slug:
        return None

    kind = str(conn.get("kind") or "rss").strip().lower()
    url = (conn.get("url") or "").strip()
    name = connection_display_name(conn)
    source_key = (conn.get("source_key") or "").strip() or None
    scraper_module = (conn.get("scraper_module") or "").strip() or None

    if kind == "rss" or (_is_rss_connection(conn) and kind not in ("builtin", "generic_site")):
        if not url:
            return None
        return {
            "id": slug,
            "name": name,
            "kind": "rss",
            "url": url,
            "source_key": source_key or "rss",
        }

    if kind == "builtin":
        mod = scraper_module or source_key
        if mod not in ("dawn", "dunya"):
            return None
        return {
            "id": slug,
            "name": name,
            "kind": "builtin",
            "url": url,
            "scraper_module": mod,
            "source_key": source_key or mod,
        }

    if kind == "generic_site":
        if not _generic_site_scrapeable(conn):
            return None
        return {
            "id": slug,
            "name": name,
            "kind": "generic_site",
            "url": url,
            "source_key": source_key,
        }

    if kind in ("currents", "newsdata", "gnews"):
        if not _api_key_configured(kind):
            return None
        return {
            "id": slug,
            "name": name,
            "kind": kind,
            "url": url,
            "source_key": source_key or kind,
        }

    return None


def list_active_scrape_targets() -> list[dict[str, Any]]:
    """Active admin connections that scrapers can run (one target per Manage Connection row)."""
    out: list[dict[str, Any]] = []
    for conn in list_connections():
        if not conn.get("active", True):
            continue
        target = connection_to_scrape_target(conn)
        if target:
            out.append(target)
    return out


def connection_display_name(conn: dict[str, Any]) -> str:
    """Human label for admin UI (fixes stored 'Feeds' names from old syncs)."""
    url = (conn.get("url") or "").strip()
    kind = str(conn.get("kind") or "rss").strip().lower()
    if kind == "rss" and url:
        return _display_name_for_feed_url(url)
    name = str(conn.get("name") or "").strip()
    if name and name.lower() != "feeds":
        return name
    if url:
        return _display_name_for_feed_url(url)
    return name or str(conn.get("slug") or "Source")


def default_connections_from_catalog() -> list[dict]:
    """Build connection rows mirroring sources_catalog (for seed / sync)."""
    out: list[dict] = []
    seen_slugs: set[str] = set()
    seen_urls: set[str] = set()
    order = 0

    def add(
        *,
        name: str,
        url: str,
        kind: str,
        scraper_module: str | None = None,
        source_key: str | None = None,
    ) -> None:
        nonlocal order
        url = (url or "").strip()
        if kind == "rss" and (not url or url in seen_urls):
            return
        if kind != "rss" and not name:
            return

        from news.platform_taxonomy import _slugify

        base_slug = _slugify(name) or _slugify(urlparse(url).netloc if url else name)
        slug = base_slug
        n = 2
        while slug in seen_slugs:
            slug = f"{base_slug}-{n}"
            n += 1
        seen_slugs.add(slug)
        if url:
            seen_urls.add(url)

        row: dict = {
            "id": slug,
            "slug": slug,
            "name": name.strip(),
            "url": url,
            "kind": kind,
            "sort_order": order,
            "active": True,
        }
        if scraper_module:
            row["scraper_module"] = scraper_module
        if source_key:
            row["source_key"] = source_key
        out.append(row)
        order += 1

    add(
        name="Dawn",
        url=DAWN_LISTING_URLS[0] if DAWN_LISTING_URLS else "https://www.dawn.com",
        kind="builtin",
        scraper_module="dawn",
        source_key="dawn",
    )
    add(
        name="Dunya News",
        url=DUNYA_LISTING_URLS[0] if DUNYA_LISTING_URLS else "https://dunyanews.tv",
        kind="builtin",
        scraper_module="dunya",
        source_key="dunya",
    )

    for site in GENERIC_SITES:
        if not site.get("enabled"):
            continue
        listing = (site.get("listing_urls") or [None])[0]
        add(
            name=str(site.get("site_display_name") or site.get("key") or "Site"),
            url=listing or str(site.get("base_url") or ""),
            kind="generic_site",
            source_key=str(site.get("source_key") or ""),
        )

    for feed_url in _catalog_rss_urls():
        add(
            name=_display_name_for_feed_url(feed_url),
            url=feed_url,
            kind="rss",
            source_key="rss",
        )

    from django.conf import settings as django_settings

    if getattr(django_settings, "CURRENTS_API_KEY", ""):
        add(
            name="Currents API",
            url="https://api.currentsapi.services/v1/latest-news",
            kind="currents",
            source_key="currents",
        )

    if getattr(django_settings, "NEWSDATA_API_KEY", ""):
        add(
            name="NewsData.io",
            url="https://newsdata.io/api/1/latest",
            kind="newsdata",
            source_key="newsdata",
        )

    if getattr(django_settings, "GNEWS_API_KEY", ""):
        add(
            name="GNews",
            url="https://gnews.io/api/v4/top-headlines",
            kind="gnews",
            source_key="gnews",
        )

    return out
