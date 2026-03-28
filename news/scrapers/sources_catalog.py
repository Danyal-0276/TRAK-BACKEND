"""
Central place for scraper *configuration* (URLs and generic-site definitions).

- **DAWN_LISTING_URLS / DUNYA_*** — used by built-in `dawn` / `dunya` sources.
- **RSS_FEED_URLS** — public Atom/RSS URLs (syndication feeds; polite way to discover
  article links). Used only when you run: `manage.py scrape_raw_news --sources rss`
- **GENERIC_SITES** — CSS + regex–driven sites. Set **enabled: True** after you tune
  selectors. Entries with **enabled: False** are stored for later and are skipped.

Built-in extractors live in `extract/`; extend via `settings.SCRAPER_*` / env as needed.

Adding a **new outlet** with custom HTML parsing: add `sources/your_site.py` + register
in `sources/__init__.py`, or enable a **GENERIC_SITES** block once selectors work.
"""

from __future__ import annotations

# --- Dawn (source: dawn) — pages linking to https://www.dawn.com/news/<id>/... ---
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
# The RSS runner **round-robins** across feeds (1st item from each feed, then 2nd…)
# so `--limit N` spreads across outlets instead of filling from the first feed only.
# Remove any feed that returns errors; respect each outlet’s terms of use.
RSS_FEED_URLS: list[str] = [
    # — International & regional news (English) —
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.theguardian.com/world/rss",
    "https://feeds.npr.org/1001/rss.xml",
    "https://feeds.apnews.com/rss/apf-topnews",
    # — Pakistan / South Asia (verify URLs if a feed moves) —
    "https://tribune.com.pk/feed/",
    "https://www.thenews.com.pk/rss/1/latest-news",
    # — Technology & industry blogs (RSS) —
    "https://techcrunch.com/feed/",
    "https://feeds.arstechnica.com/arstechnica/index",
    "https://www.theverge.com/rss/index.xml",
    "https://www.wired.com/feed/rss",
    # — Developer / community —
    "https://dev.to/feed",
]

# --- Config-driven sites (source: generic_sites) — merged with settings + JSON ---
# Set **enabled: False** to skip a site without removing its config.
# **site_display_name** — shown in MongoDB `extra.site_display_name` (which outlet / website).
# `fallback_generic: true` helps when selectors drift.
GENERIC_SITES: list[dict] = [
    {
        "key": "express_tribune",
        "enabled": True,
        "source_key": "generic_express_tribune",
        "site_display_name": "Express Tribune",
        "base_url": "https://tribune.com.pk",
        "listing_urls": [
            "https://tribune.com.pk/latest/",
        ],
        # Match story URLs; strip trailing slash optional
        "article_href_regex": r"https://(www\.)?tribune\.com\.pk/story/\d+",
        "selectors": {
            "title": "h1",
            # `.story-content` is often an empty wrapper; story text lives in `.express-parent-div`
            "body": "div.express-parent-div, .story-content .express-parent-div, .story-content, article, main",
            "published": "time",
            "published_attr": "datetime",
            "summary": "meta[property='og:description']",
            "image": "meta[property='og:image']",
        },
        "fallback_generic": True,
        "max_per_site": 15,
    },
    {
        "key": "the_news_pk",
        "enabled": True,
        "source_key": "generic_the_news_pk",
        "site_display_name": "The News International",
        "base_url": "https://www.thenews.com.pk",
        "listing_urls": [
            "https://www.thenews.com.pk/latest/category/national",
        ],
        # Current site uses /latest/<numeric-id>-slug (not only /print/ or date paths).
        "article_href_regex": r"https://www\.thenews\.com\.pk/latest/\d+|https://www\.thenews\.com\.pk/(print|article)/\d+|https://www\.thenews\.com\.pk/\d{2}/\d{2}/\d{4}/",
        "selectors": {
            "title": "h1",
            "body": ".story-detail, article, main, .detail-desc",
            "summary": "meta[property='og:description']",
            "image": "meta[property='og:image']",
            # Redundant with JSON-LD backfill in extract_site_config; helps if ld is absent.
            "published": "meta[property='article:published_time']",
        },
        "fallback_generic": True,
        "max_per_site": 15,
    },
    {
        "key": "geo_news_en",
        "enabled": True,
        "source_key": "generic_geo_news_en",
        "site_display_name": "Geo News",
        "base_url": "https://www.geo.tv",
        # `/category/english-news` returns empty HTML to non-browser clients (SPA shell).
        # Homepage serves full markup with `/latest/<id>-slug` story links.
        "listing_urls": [
            "https://www.geo.tv/",
        ],
        "article_href_regex": r"https://(www\.)?geo\.tv/latest/\d+",
        "selectors": {
            "title": "h1",
            "body": ".detail-body, .content-area, article, main",
            "summary": "meta[property='og:description']",
            "image": "meta[property='og:image']",
        },
        "fallback_generic": True,
        "max_per_site": 15,
    },
    {
        "key": "medium_publication_template",
        "enabled": False,
        "source_key": "generic_blog_template",
        "site_display_name": "Medium",
        "base_url": "https://medium.com",
        "listing_urls": [],
        "article_href_regex": r"https://medium\.com/@[^/]+/[\w-]+",
        "selectors": {
            "title": "h1",
            "body": "article section",
        },
        "fallback_generic": True,
        "max_per_site": 10,
    },
]
