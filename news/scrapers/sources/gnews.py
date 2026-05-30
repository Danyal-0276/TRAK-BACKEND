"""GNews API (gnews.io) — top headlines JSON feed."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from django.conf import settings

from news.scrapers.client import PoliteHttpClient
from news.scrapers.document import build_article_document
from news.scrapers.extract.generic import extract_generic
from news.scrapers.extract.utils import normalize_ws, parse_published_datetime
from news.scrapers import robots as robots_util
from news.scrapers import storage
from news.scrapers.site_key import source_key_for_article_url

BASE_URL = "https://gnews.io/api/v4"
VALID_CATEGORIES = frozenset({
    "general",
    "world",
    "nation",
    "business",
    "technology",
    "entertainment",
    "sports",
    "science",
    "health",
})


def _api_key() -> str:
    return (getattr(settings, "GNEWS_API_KEY", "") or "").strip()


def _json_headers() -> dict[str, str]:
    return {"Accept": "application/json"}


def _page_size() -> int:
    size = int(getattr(settings, "GNEWS_API_MAX", 10))
    return max(1, min(size, 10))


def _source_name(item: dict[str, Any]) -> str | None:
    source = item.get("source")
    if isinstance(source, dict):
        name = normalize_ws(source.get("name") or "")
        return name or None
    if source:
        return normalize_ws(str(source)) or None
    return None


def _body_text(item: dict[str, Any], *, title: str) -> str:
    for key in ("content", "description"):
        text = normalize_ws(item.get(key) or "")
        if text:
            return text
    return title


def _extracted_from_api_item(item: dict[str, Any]) -> dict[str, Any] | None:
    url = (item.get("url") or "").strip()
    title = normalize_ws(item.get("title") or "")
    if not url or not title:
        return None

    description = normalize_ws(item.get("description") or "")
    body = _body_text(item, title=title)

    return {
        "title": title,
        "summary": (description or body)[:500] if (description or body) else None,
        "body_text": body,
        "published_at": parse_published_datetime(item.get("publishedAt")),
        "author_name": _source_name(item),
        "category": None,
        "image_url": (item.get("image") or "").strip() or None,
    }


def _fetch_articles(
    client: PoliteHttpClient,
    params: dict[str, str],
) -> tuple[list[dict[str, Any]], str | None]:
    params = {**params, "apikey": _api_key()}
    url = f"{BASE_URL}/top-headlines?{urlencode({k: v for k, v in params.items() if v})}"
    try:
        response = client.get(url, extra_headers=_json_headers())
    except Exception as exc:
        return [], str(exc)
    if response.status_code == 429:
        return [], "daily request limit reached (429)"
    if response.status_code in (401, 403):
        return [], "invalid GNEWS_API_KEY (401/403)"
    if response.status_code != 200:
        return [], f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return [], "invalid JSON response"
    errors = payload.get("errors")
    if errors:
        if isinstance(errors, list):
            return [], "; ".join(str(e) for e in errors)
        return [], str(errors)
    articles = payload.get("articles") or []
    return [a for a in articles if isinstance(a, dict)], None


def _collect_items(
    client: PoliteHttpClient,
    *,
    language: str,
    country: str,
    categories: list[str],
    page_size: int,
    max_requests: int,
) -> tuple[list[dict[str, Any]], int, list[str], dict[str, str]]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    item_categories: dict[str, str] = {}
    errors: list[str] = []
    requests_used = 0

    def add_batch(batch: list[dict[str, Any]], category: str) -> None:
        for item in batch:
            key = str(item.get("id") or item.get("url") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)
            item_categories[key] = category

    def fetch(category: str, label: str) -> None:
        nonlocal requests_used
        if requests_used >= max_requests:
            errors.append("stopped early: GNEWS_API_MAX_REQUESTS_PER_RUN reached")
            return
        params: dict[str, str] = {
            "lang": language,
            "max": str(page_size),
            "category": category,
        }
        if country:
            params["country"] = country
        batch, err = _fetch_articles(client, params)
        requests_used += 1
        if err:
            errors.append(f"{label}: {err}")
        else:
            add_batch(batch, category)

    fetch("general", "top-headlines/general")

    for category in categories:
        if category == "general":
            continue
        if category not in VALID_CATEGORIES:
            errors.append(f"skipped invalid category: {category}")
            continue
        if requests_used >= max_requests:
            errors.append("stopped early: GNEWS_API_MAX_REQUESTS_PER_RUN reached")
            break
        fetch(category, f"top-headlines/{category}")

    return items, requests_used, errors, item_categories


def _maybe_fetch_page(
    client: PoliteHttpClient,
    *,
    url: str,
    title_hint: str,
    ua: str,
) -> tuple[dict[str, Any] | None, int, str]:
    if not robots_util.allowed(url, ua):
        return None, 0, ""
    try:
        response = client.get(url)
    except Exception:
        return None, 0, ""
    if response.status_code != 200:
        return None, response.status_code, response.headers.get("content-type", "")
    body = response.text
    if len(body.encode("utf-8")) > settings.SCRAPER_MAX_HTML_BYTES:
        return None, response.status_code, response.headers.get("content-type", "")
    extracted = extract_generic(body, url, fallback_title=title_hint)
    if not extracted:
        return None, response.status_code, response.headers.get("content-type", "")
    return extracted, response.status_code, response.headers.get("content-type", "")


def run(client: PoliteHttpClient, *, limit: int = 30) -> dict:
    if not _api_key():
        return {
            "inserted": 0,
            "skipped": 0,
            "source": "gnews",
            "note": "GNEWS_API_KEY not set — add it to .env",
        }

    language = (getattr(settings, "GNEWS_API_LANGUAGE", "en") or "en").strip()
    country = (getattr(settings, "GNEWS_API_COUNTRY", "") or "").strip()
    categories = list(getattr(settings, "GNEWS_API_CATEGORIES", []) or [])
    page_size = _page_size()
    max_requests = max(1, int(getattr(settings, "GNEWS_API_MAX_REQUESTS_PER_RUN", 4)))
    fetch_pages = bool(getattr(settings, "GNEWS_API_FETCH_ARTICLE_PAGES", False))
    ua = settings.SCRAPER_USER_AGENT

    items, requests_used, errors, item_categories = _collect_items(
        client,
        language=language,
        country=country,
        categories=categories,
        page_size=page_size,
        max_requests=max_requests,
    )
    if not items and errors:
        return {
            "inserted": 0,
            "skipped": 0,
            "source": "gnews",
            "note": "; ".join(errors),
            "api_requests": requests_used,
        }

    inserted = 0
    skipped = 0
    for item in items:
        if inserted >= limit:
            break
        extracted = _extracted_from_api_item(item)
        if not extracted:
            skipped += 1
            continue
        url = (item.get("url") or "").strip()
        if storage.exists_url(url):
            skipped += 1
            continue

        item_key = str(item.get("id") or url).strip()
        category = item_categories.get(item_key)
        if category:
            extracted["category"] = category

        http_status = 200
        content_type = "application/json"
        raw_html = None
        if fetch_pages:
            page_extracted, http_status, content_type = _maybe_fetch_page(
                client,
                url=url,
                title_hint=extracted["title"],
                ua=ua,
            )
            if page_extracted:
                extracted = page_extracted

        source_meta = item.get("source") if isinstance(item.get("source"), dict) else {}
        sk = source_key_for_article_url(url)
        doc = build_article_document(
            canonical_url=url,
            source_key=sk,
            extracted=extracted,
            http_status=http_status,
            content_type=content_type,
            extra={
                "gnews_id": item.get("id"),
                "ingestion_channel": "gnews_api",
                "gnews_language": item.get("lang") or language,
                "gnews_category": category,
                "gnews_source_name": source_meta.get("name"),
                "gnews_source_url": source_meta.get("url"),
            },
            raw_html=raw_html,
        )
        if storage.insert_raw_if_new(doc):
            inserted += 1
        else:
            skipped += 1

    result: dict[str, Any] = {
        "inserted": inserted,
        "skipped": skipped,
        "source": "gnews",
        "api_requests": requests_used,
        "candidates": len(items),
    }
    if errors:
        result["warnings"] = errors
    return result
