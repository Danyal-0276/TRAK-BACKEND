"""Build MongoDB documents from extracted article dicts."""

from __future__ import annotations

from typing import Any

from django.conf import settings

from news.scrapers.site_key import display_name_for_source_key


def build_article_document(
    *,
    canonical_url: str,
    source_key: str,
    extracted: dict[str, Any],
    http_status: int,
    content_type: str,
    extra: dict[str, Any],
    raw_html: str | None = None,
) -> dict[str, Any]:
    data = dict(extracted)
    links = data.pop("links", []) or []
    extra = {**extra, "links": links}
    if "site_display_name" not in extra:
        extra["site_display_name"] = display_name_for_source_key(source_key)

    doc: dict[str, Any] = {
        "canonical_url": canonical_url,
        "source_key": source_key,
        "title": data["title"],
        "summary": data.get("summary"),
        "body_text": data["body_text"],
        "published_at": data.get("published_at"),
        "author_name": data.get("author_name"),
        "author_url": data.get("author_url"),
        "category": data.get("category"),
        "image_url": data.get("image_url"),
        "http_status": http_status,
        "content_type": content_type,
        "extra": extra,
        "pipeline_status": "pending",
    }

    if getattr(settings, "SCRAPER_STORE_RAW_HTML", False) and raw_html is not None:
        doc["raw_html"] = raw_html

    return doc
