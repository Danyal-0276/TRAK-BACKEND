"""RSS/Atom feeds — preferred when a site publishes an open feed (blogs, some outlets)."""

from __future__ import annotations

import feedparser
from django.conf import settings

from news.scrapers.client import PoliteHttpClient
from news.scrapers.document import build_article_document
from news.scrapers.extract.generic import extract_generic
from news.scrapers import robots as robots_util
from news.scrapers import storage
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

    inserted = 0
    skipped = 0

    for feed_url in feeds:
        if inserted >= limit:
            break
        if not robots_util.allowed(feed_url, ua):
            continue
        fr = client.get(feed_url)
        if fr.status_code != 200:
            continue
        parsed = feedparser.parse(fr.text)
        for entry in parsed.entries:
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
                continue
            r = client.get(link)
            if r.status_code != 200:
                continue
            body = r.text
            if len(body.encode("utf-8")) > settings.SCRAPER_MAX_HTML_BYTES:
                continue
            title_hint = getattr(entry, "title", "") or ""
            extracted = extract_generic(body, link, fallback_title=title_hint)
            if not extracted:
                skipped += 1
                continue
            doc = build_article_document(
                canonical_url=link,
                source_key="rss",
                extracted=extracted,
                http_status=r.status_code,
                content_type=r.headers.get("content-type", ""),
                extra={
                    "feed_url": feed_url,
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
