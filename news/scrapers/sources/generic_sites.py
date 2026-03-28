"""
Configurable multi-site scraper. Defaults live in `news/scrapers/sources_catalog.py`
(GENERIC_SITES); optional extras in settings / JSON — see `_load_site_configs`.

Each entry defines listing URLs, how to find article links, and CSS selectors for article pages.

Example (settings.py or JSON list):

    {
        "key": "my_blog",
        "base_url": "https://example.com",
        "listing_urls": ["https://example.com/category/news/"],
        "article_href_regex": r"https://example\\.com/20\\d{2}/\\d{2}/[\\w-]+/",
        "article_link_css": "article h2 a",  # optional; if omitted, all <a> are tested
        "selectors": {
            "title": "h1.entry-title",
            "body": "div.entry-content",
            "author": "span.author a",
            "author_is_link": true,
            "published": "time[datetime]",
            "published_attr": "datetime",
            "summary": "meta[property='og:description']",
            "image": "meta[property='og:image']",
        },
        "fallback_generic": true
    }
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from django.conf import settings

from news.scrapers.client import PoliteHttpClient
from news.scrapers.document import build_article_document
from news.scrapers.extract.site_config import extract_site_config
from news.scrapers import robots as robots_util
from news.scrapers import storage
from news.scrapers.sources_catalog import GENERIC_SITES as CATALOG_GENERIC_SITES


def _load_site_configs() -> list[dict]:
    out: list[dict] = list(CATALOG_GENERIC_SITES)
    out.extend(list(getattr(settings, "SCRAPER_GENERIC_SOURCES", None) or []))
    path = getattr(settings, "SCRAPER_GENERIC_SOURCES_JSON", None)
    if path:
        p = Path(path)
        if not p.is_absolute():
            p = Path(settings.BASE_DIR) / p
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                out.extend(data)
            elif isinstance(data, dict) and "sites" in data:
                out.extend(data["sites"])
    return out


def _normalize(url: str) -> str:
    u, _ = urldefrag(url.strip())
    return u.rstrip("/")


def discover_article_urls(listing_html: str, listing_url: str, cfg: dict) -> list[str]:
    base = (cfg.get("base_url") or "").strip()
    if not base:
        p = urlparse(listing_url)
        base = f"{p.scheme}://{p.netloc}"
    pattern = cfg.get("article_href_regex")
    if not pattern:
        return []
    rx = re.compile(pattern)
    excl = cfg.get("exclude_href_regex")
    exrx = re.compile(excl) if excl else None

    soup = BeautifulSoup(listing_html, "html.parser")
    seen: set[str] = set()
    out: list[str] = []

    def consider(href: str) -> None:
        abs_u = _normalize(urljoin(base, href))
        if not rx.search(abs_u):
            return
        if exrx and exrx.search(abs_u):
            return
        if abs_u not in seen:
            seen.add(abs_u)
            out.append(abs_u)

    css_sel = (cfg.get("article_link_css") or "").strip()
    if css_sel:
        for a in soup.select(css_sel):
            href = a.get("href")
            if href:
                consider(href)
    else:
        for a in soup.find_all("a", href=True):
            consider(a["href"])

    return sorted(out)


def run(client: PoliteHttpClient, *, limit: int = 30) -> dict:
    ua = settings.SCRAPER_USER_AGENT
    configs = _load_site_configs()
    if not configs:
        return {
            "inserted": 0,
            "skipped": 0,
            "source": "generic_sites",
            "note": "no generic sites — add entries in sources_catalog.py (GENERIC_SITES) or settings/JSON",
        }

    inserted = 0
    skipped = 0

    for cfg in configs:
        if inserted >= limit:
            break
        key = (cfg.get("key") or "site").strip() or "site"
        source_key = cfg.get("source_key") or f"generic_{key}"
        per_site_limit = int(cfg.get("max_per_site") or limit)

        for listing in cfg.get("listing_urls") or []:
            if inserted >= limit:
                break
            listing = listing.strip()
            if not listing:
                continue
            if not robots_util.allowed(listing, ua):
                continue
            lr = client.get(listing)
            if lr.status_code != 200:
                continue
            urls = discover_article_urls(lr.text, listing, cfg)
            n_site = 0
            for url in urls:
                if inserted >= limit or n_site >= per_site_limit:
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
                extracted = extract_site_config(body, url, cfg)
                if not extracted:
                    skipped += 1
                    continue
                doc = build_article_document(
                    canonical_url=url,
                    source_key=source_key,
                    extracted=extracted,
                    http_status=ar.status_code,
                    content_type=ar.headers.get("content-type", ""),
                    extra={
                        "listing_url": listing,
                        "site_key": key,
                        "robots_allowed": True,
                    },
                    raw_html=body if settings.SCRAPER_STORE_RAW_HTML else None,
                )
                ok = storage.insert_raw_if_new(doc)
                if ok:
                    inserted += 1
                    n_site += 1
                else:
                    skipped += 1

    return {"inserted": inserted, "skipped": skipped, "source": "generic_sites"}
