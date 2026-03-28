"""Dunya News — English category listings + article pages."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urldefrag

from bs4 import BeautifulSoup
from django.conf import settings

from news.scrapers.client import PoliteHttpClient
from news.scrapers.document import build_article_document
from news.scrapers.extract.dunya import extract_dunya
from news.scrapers import robots as robots_util
from news.scrapers import storage
from news.scrapers.site_key import source_key_for_article_url
from news.scrapers.sources_catalog import DUNYA_BASE_URL as BASE
from news.scrapers.sources_catalog import DUNYA_LISTING_URLS as LISTING_URLS

# /index.php/en/Pakistan/942890-slug
DUNYA_PATH_RE = re.compile(r"^/index\.php/en/[A-Za-z]+/\d+-")


def _normalize(url: str) -> str:
    url, _ = urldefrag(url.strip())
    return url.rstrip("/")


def discover_article_urls(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue
        if href.startswith("/"):
            path = href.split("?", 1)[0]
            if DUNYA_PATH_RE.match(path):
                out.add(_normalize(urljoin(BASE, path)))
        elif "dunyanews.tv" in href and "/index.php/en/" in href:
            p = href.split("dunyanews.tv", 1)[-1].split("?", 1)[0]
            if DUNYA_PATH_RE.match(p):
                out.add(_normalize(urljoin(BASE, p)))
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
            extracted = extract_dunya(body, url)
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
                    "ingestion_channel": "dunya_listings",
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

    return {"inserted": inserted, "skipped": skipped, "source": "dunya"}
