"""RSS/Atom feeds — round-robin across feeds so each run hits many outlets, not only the first feed."""

from __future__ import annotations

from typing import Any

import feedparser
from django.conf import settings

from news.scrapers.client import PoliteHttpClient
from news.scrapers.document import build_article_document
from news.scrapers.extract.generic import extract_generic
from news.scrapers import robots as robots_util
from news.scrapers import storage
from news.scrapers.site_key import source_key_for_article_url
from news.scrapers.sources_catalog import RSS_FEED_URLS as CATALOG_RSS_FEEDS


def _merged_feed_urls() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in CATALOG_RSS_FEEDS + list(getattr(settings, "SCRAPER_RSS_FEED_URLS", []) or []):
        u = (u or "").strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _round_robin_entries(
    client: PoliteHttpClient,
    feeds: list[str],
    ua: str,
) -> list[tuple[str, Any]]:
    """
    Fetch each feed, then interleave entries: 1st from feed A, 1st from feed B, …,
    then 2nd from A, 2nd from B, … so a single `--limit` spreads across websites.
    """
    by_feed: dict[str, list[Any]] = {}
    for feed_url in feeds:
        if not robots_util.allowed(feed_url, ua):
            continue
        fr = client.get(feed_url)
        if fr.status_code != 200:
            continue
        parsed = feedparser.parse(fr.text)
        if parsed.entries:
            by_feed[feed_url] = list(parsed.entries)

    if not by_feed:
        return []

    order = list(by_feed.keys())
    max_len = max(len(by_feed[f]) for f in order)
    out: list[tuple[str, Any]] = []
    for i in range(max_len):
        for f in order:
            if i < len(by_feed[f]):
                out.append((f, by_feed[f][i]))
    return out


def run(client: PoliteHttpClient, *, limit: int = 30) -> dict:
    ua = settings.SCRAPER_USER_AGENT
    feeds = _merged_feed_urls()
    if not feeds:
        return {
            "inserted": 0,
            "skipped": 0,
            "source": "rss",
            "note": "no RSS feeds — add URLs in news/scrapers/sources_catalog.py (RSS_FEED_URLS) or settings/env",
        }

    queue = _round_robin_entries(client, feeds, ua)
    inserted = 0
    skipped = 0

    for feed_url, entry in queue:
        if inserted >= limit:
            break
        link = getattr(entry, "link", None) or ""
        link = (link or "").strip()
        if not link:
            continue
        if storage.exists_url(link):
            skipped += 1
            continue
        if not robots_util.allowed(link, ua):
            skipped += 1
            continue
        r = client.get(link)
        if r.status_code != 200:
            skipped += 1
            continue
        body = r.text
        if len(body.encode("utf-8")) > settings.SCRAPER_MAX_HTML_BYTES:
            skipped += 1
            continue
        title_hint = getattr(entry, "title", "") or ""
        extracted = extract_generic(body, link, fallback_title=title_hint)
        if not extracted:
            skipped += 1
            continue
        sk = source_key_for_article_url(link)
        doc = build_article_document(
            canonical_url=link,
            source_key=sk,
            extracted=extracted,
            http_status=r.status_code,
            content_type=r.headers.get("content-type", ""),
            extra={
                "feed_url": feed_url,
                "entry_title": (title_hint or "")[:500],
                "ingestion_channel": "rss",
                "robots_allowed": True,
            },
            raw_html=body if settings.SCRAPER_STORE_RAW_HTML else None,
        )
        ok = storage.insert_raw_if_new(doc)
        if ok:
            inserted += 1
        else:
            skipped += 1

    return {"inserted": inserted, "skipped": skipped, "source": "rss"}
