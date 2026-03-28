"""
Central place for scraper *configuration* (URLs and generic-site definitions).

- **Dawn / Dunya listing URLs** — add section homepages or category pages here.
- **RSS_FEED_URLS** — add Atom/RSS feed URLs for the `rss` source.
- **GENERIC_SITES** — add config-driven sites for `generic_sites` (selectors + regex).

Built-in extractors (`dawn`, `dunya`) still live in `extract/`; this file only holds
data. You can also extend feeds/sources via `settings.SCRAPER_*` and env vars (merged
with the lists below).

To add a **new outlet** that needs custom Python parsing, add a new module under
`sources/` and register it in `sources/__init__.py` — or use **GENERIC_SITES** if
CSS selectors are enough.
"""

from __future__ import annotations

# --- Dawn (source: dawn) — pages that link to https://www.dawn.com/news/<id>/... ---
DAWN_LISTING_URLS = [
    "https://www.dawn.com/latest-news",
    "https://www.dawn.com/pakistan",
    "https://www.dawn.com/world",
    "https://www.dawn.com/business",
]

# --- Dunya News English (source: dunya) ---
DUNYA_BASE_URL = "https://dunyanews.tv"
DUNYA_LISTING_URLS = [
    "https://dunyanews.tv/index.php/en/Pakistan",
    "https://dunyanews.tv/index.php/en/World",
    "https://dunyanews.tv/index.php/en/Business",
]

# --- RSS / Atom (source: rss) — merged with settings.SCRAPER_RSS_FEED_URLS ---
RSS_FEED_URLS: list[str] = [
    # "https://example.com/feed.xml",
]

# --- Config-driven sites (source: generic_sites) — merged with settings + optional JSON file ---
# Same schema as documented in `sources/generic_sites.py`.
GENERIC_SITES: list[dict] = [
    # {
    #     "key": "my_site",
    #     "source_key": "generic_my_site",
    #     "base_url": "https://example.com",
    #     "listing_urls": ["https://example.com/news/"],
    #     "article_href_regex": r"https://example\.com/\d{4}/[\w/-]+",
    #     "selectors": {"title": "h1", "body": "article"},
    # },
]
