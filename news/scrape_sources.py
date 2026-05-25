"""Scrape source URLs from admin connections (MongoDB) with catalog fallback."""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings

from news.platform_taxonomy import list_connections
from news.scrapers.sources_catalog import (
    DAWN_LISTING_URLS,
    DUNYA_LISTING_URLS,
    GENERIC_SITES,
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
    if out:
        return out
    if fallback_to_catalog:
        return _catalog_rss_urls()
    return []


def _display_name_for_feed_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.replace("www.", "")
        if not host:
            return "RSS feed"
        label = host.split(".")[0]
        return label.replace("-", " ").title()
    except Exception:
        return "RSS feed"


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

    return out
