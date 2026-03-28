"""Derive stable, human-readable source_key values from article URLs."""

from __future__ import annotations

from urllib.parse import urlparse

# Hostname (no www.) -> short id used as MongoDB source_key (not the literal word "rss").
_HOST_TO_SOURCE_KEY: dict[str, str] = {
    "dawn.com": "dawn_news",
    "dunyanews.tv": "dunya_news",
    "aljazeera.com": "aljazeera",
    "bbc.co.uk": "bbc_news",
    "bbc.com": "bbc_news",
    "theguardian.com": "the_guardian",
    "npr.org": "npr",
    "apnews.com": "ap_news",
    "thenews.com.pk": "the_news_pk",
    "tribune.com.pk": "express_tribune",
    "techcrunch.com": "techcrunch",
    "arstechnica.com": "ars_technica",
    "theverge.com": "the_verge",
    "wired.com": "wired",
    "dev.to": "dev_to",
    "medium.com": "medium",
    "reuters.com": "reuters",
    "cnn.com": "cnn",
    "nytimes.com": "nytimes",
    "washingtonpost.com": "washington_post",
    "geo.tv": "geo_news",
}

# source_key -> display label for UIs / Compass
SOURCE_KEY_DISPLAY_NAME: dict[str, str] = {
    "dawn_news": "Dawn News",
    "dunya_news": "Dunya News",
    "aljazeera": "Al Jazeera English",
    "bbc_news": "BBC News",
    "the_guardian": "The Guardian",
    "npr": "NPR",
    "ap_news": "Associated Press",
    "the_news_pk": "The News International",
    "express_tribune": "Express Tribune",
    "techcrunch": "TechCrunch",
    "ars_technica": "Ars Technica",
    "the_verge": "The Verge",
    "wired": "Wired",
    "dev_to": "DEV Community",
    "medium": "Medium",
    "reuters": "Reuters",
    "cnn": "CNN",
    "nytimes": "The New York Times",
    "washington_post": "The Washington Post",
    "geo_news": "Geo News",
    # generic_sites config source_key values (see sources_catalog.GENERIC_SITES)
    "generic_express_tribune": "Express Tribune",
    "generic_the_news_pk": "The News International",
    "generic_geo_news_en": "Geo News",
    "generic_blog_template": "Medium",
}


def site_display_name_for_generic(cfg: dict, article_url: str, source_key: str) -> str:
    """
    Human-readable label for a generic-site scraper: config override, then lookup,
    then hostname from the article URL.
    """
    explicit = (cfg.get("site_display_name") or "").strip()
    if explicit:
        return explicit
    if source_key in SOURCE_KEY_DISPLAY_NAME:
        return SOURCE_KEY_DISPLAY_NAME[source_key]
    return display_name_for_source_key(source_key_for_article_url(article_url))


def hostname_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    # m.aljazeera.com -> aljazeera.com for mapping lookup
    if host.startswith("m.") and host.count(".") >= 2:
        host = host[2:]
    return host


def source_key_for_article_url(url: str) -> str:
    """
    Map article URL to a stable source_key (per publisher / site), not ingestion channel.
    Unknown hosts: ``news_example_com`` style from the hostname.
    """
    host = hostname_from_url(url)
    if host in _HOST_TO_SOURCE_KEY:
        return _HOST_TO_SOURCE_KEY[host]
    # bbc.co.uk vs www.bbc - already stripped www
    safe = host.replace(".", "_")
    if safe and safe[0].isdigit():
        safe = "site_" + safe
    return safe or "unknown_site"


def display_name_for_source_key(source_key: str) -> str:
    if source_key in SOURCE_KEY_DISPLAY_NAME:
        return SOURCE_KEY_DISPLAY_NAME[source_key]
    return source_key.replace("_", " ").title()
