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
# These are standard syndication feeds (not scraped HTML homepages). Remove any feed
# that returns errors; respect each outlet’s terms of use.
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
# Use **enabled: False** until `article_href_regex` and `selectors` are tested.
# `fallback_generic: true` helps when selectors drift.
GENERIC_SITES: list[dict] = [
    {
        "key": "express_tribune",
        "enabled": False,
        "source_key": "generic_express_tribune",
        "base_url": "https://tribune.com.pk",
        "listing_urls": [
            "https://tribune.com.pk/latest/",
        ],
        "article_href_regex": r"https://tribune\.com\.pk/story/\d+/",
        "article_link_css": "article h2 a, .latest-news-section a",
        "selectors": {
            "title": "h1",
            "body": "div.clearfix.story-content, article .story-detail",
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
        "enabled": False,
        "source_key": "generic_the_news_pk",
        "base_url": "https://www.thenews.com.pk",
        "listing_urls": [
            "https://www.thenews.com.pk/latest/category/national",
        ],
        "article_href_regex": r"https://www\.thenews\.com\.pk/\d{2}/\d{2}/\d{4}/",
        "selectors": {
            "title": "h1.detail-heading",
            "body": "div.detail-desc",
            "summary": "meta[property='og:description']",
            "image": "meta[property='og:image']",
        },
        "fallback_generic": True,
        "max_per_site": 15,
    },
    {
        "key": "geo_news_en",
        "enabled": False,
        "source_key": "generic_geo_news_en",
        "base_url": "https://www.geo.tv",
        "listing_urls": [
            "https://www.geo.tv/category/english-news",
        ],
        "article_href_regex": r"https://www\.geo\.tv/latest/\d+",
        "selectors": {
            "title": "h1",
            "body": "article, .content-area",
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
