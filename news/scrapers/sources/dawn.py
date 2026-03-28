"""Dawn — listing pages + article pages (www.dawn.com/news/...)."""

from __future__ import annotations

import re
from urllib.parse import urldefrag

from bs4 import BeautifulSoup
from django.conf import settings

from news.scrapers.client import PoliteHttpClient
from news.scrapers.document import build_article_document
from news.scrapers.extract.dawn import extract_dawn
from news.scrapers import robots as robots_util
from news.scrapers import storage
from news.scrapers.site_key import source_key_for_article_url
from news.scrapers.sources_catalog import DAWN_LISTING_URLS as LISTING_URLS

# Main news articles only (exclude images.dawn.com microsites if desired)
DAWN_ARTICLE_RE = re.compile(r"^https://www\.dawn\.com/news/\d+")


def _normalize(url: str) -> str:
    url, _frag = urldefrag(url.strip())
    return url.rstrip("/")


def discover_article_urls(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: set[str] = set()
    for a in soup.select("a.story__link[href]"):
        href = a.get("href") or ""
        if DAWN_ARTICLE_RE.match(href):
            out.add(_normalize(href))
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if DAWN_ARTICLE_RE.match(href):
            out.add(_normalize(href))
    return sorted(out)


def run(client: PoliteHttpClient, *, limit: int = 30) -> dict:
    ua = settings.SCRAPER_USER_AGENT
    inserted = 0
    skipped = 0
    seen: set[str] = set()

    for listing in LISTING_URLS:
        if not robots_util.allowed(listing, ua):
            continue
        r = client.get(listing)
        if r.status_code != 200:
            continue
        urls = discover_article_urls(r.text)
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            if inserted >= limit:
                break
            if storage.exists_url(url):
                skipped += 1
                continue
            if not robots_util.allowed(url, ua):
                continue
            ar = client.get(url)
            if ar.status_code != 200:
                continue
            body = ar.text
            if len(body.encode("utf-8")) > settings.SCRAPER_MAX_HTML_BYTES:
                continue
            extracted = extract_dawn(body, url)
            if not extracted:
                skipped += 1
                continue
            doc = build_article_document(
                canonical_url=url,
                source_key=source_key_for_article_url(url),
                extracted=extracted,
                http_status=ar.status_code,
                content_type=ar.headers.get("content-type", ""),
                extra={
                    "listing_url": listing,
                    "ingestion_channel": "dawn_listings",
                    "robots_allowed": True,
                },
                raw_html=body if settings.SCRAPER_STORE_RAW_HTML else None,
            )
            ok = storage.insert_raw_if_new(doc)
            if ok:
                inserted += 1
            else:
                skipped += 1
        if inserted >= limit:
            break

    return {"inserted": inserted, "skipped": skipped, "source": "dawn"}
