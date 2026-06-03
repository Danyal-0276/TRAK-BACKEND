"""NewsData.io API — latest global news JSON feed."""

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

def _base_url() -> str:
    return (getattr(settings, "NEWSDATA_API_BASE_URL", None) or "").strip().rstrip("/")


def _api_key() -> str:
    return (getattr(settings, "NEWSDATA_API_KEY", "") or "").strip()


def _json_headers() -> dict[str, str]:
    return {"Accept": "application/json"}


def _page_size() -> int:
    size = int(getattr(settings, "NEWSDATA_API_SIZE", 10))
    return max(1, min(size, 10))


def _author_name(item: dict[str, Any]) -> str | None:
    creator = item.get("creator")
    if isinstance(creator, list):
        parts = [normalize_ws(str(c)) for c in creator if c]
        parts = [p for p in parts if p]
        if parts:
            return ", ".join(parts)
    elif creator:
        name = normalize_ws(str(creator))
        if name:
            return name
    source_name = normalize_ws(item.get("source_name") or "")
    return source_name or None


def _category_name(item: dict[str, Any]) -> str | None:
    category = item.get("category")
    if isinstance(category, list):
        return category[0] if category else None
    if category:
        return str(category)
    return None


def _body_text(item: dict[str, Any], *, title: str) -> str:
    for key in ("content", "description", "ai_summary"):
        text = normalize_ws(item.get(key) or "")
        if text and text.lower() != "only available in paid plans":
            return text
    return title


def _extracted_from_api_item(item: dict[str, Any]) -> dict[str, Any] | None:
    url = (item.get("link") or item.get("url") or "").strip()
    title = normalize_ws(item.get("title") or "")
    if not url or not title:
        return None

    description = normalize_ws(item.get("description") or "")
    body = _body_text(item, title=title)

    return {
        "title": title,
        "summary": (description or body)[:500] if (description or body) else None,
        "body_text": body,
        "published_at": parse_published_datetime(item.get("pubDate")),
        "author_name": _author_name(item),
        "category": _category_name(item),
        "image_url": (item.get("image_url") or "").strip() or None,
    }


def _fetch_news(
    client: PoliteHttpClient,
    path: str,
    params: dict[str, str],
) -> tuple[list[dict[str, Any]], str | None]:
    params = {**params, "apikey": _api_key()}
    base = _base_url()
    if not base:
        return [], "NEWSDATA_API_BASE_URL not set in .env"
    url = f"{base}{path}?{urlencode({k: v for k, v in params.items() if v})}"
    try:
        response = client.get(url, extra_headers=_json_headers())
    except Exception as exc:
        return [], str(exc)
    if response.status_code == 429:
        return [], "daily request limit reached (429)"
    if response.status_code in (401, 403):
        return [], "invalid NEWSDATA_API_KEY (401/403)"
    if response.status_code != 200:
        return [], f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return [], "invalid JSON response"
    status = str(payload.get("status") or "").lower()
    if status and status not in ("success", "ok"):
        return [], str(payload.get("message") or payload.get("results") or status)
    results = payload.get("results") or []
    return [n for n in results if isinstance(n, dict)], None


def _collect_items(
    client: PoliteHttpClient,
    *,
    language: str,
    country: str,
    categories: list[str],
    page_size: int,
    max_requests: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    requests_used = 0

    def add_batch(batch: list[dict[str, Any]]) -> None:
        for item in batch:
            key = str(item.get("article_id") or item.get("link") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)

    def fetch(path: str, params: dict[str, str], label: str) -> None:
        nonlocal requests_used
        if requests_used >= max_requests:
            errors.append("stopped early: NEWSDATA_API_MAX_REQUESTS_PER_RUN reached")
            return
        batch, err = _fetch_news(client, path, params)
        requests_used += 1
        if err:
            errors.append(f"{label}: {err}")
        else:
            add_batch(batch)

    base_params: dict[str, str] = {
        "language": language,
        "size": str(page_size),
    }
    if country:
        base_params["country"] = country

    fetch("/latest", base_params, "latest")

    for category in categories:
        if requests_used >= max_requests:
            errors.append("stopped early: NEWSDATA_API_MAX_REQUESTS_PER_RUN reached")
            break
        fetch("/latest", {**base_params, "category": category}, f"latest/{category}")

    return items, requests_used, errors


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
            "source": "newsdata",
            "note": "NEWSDATA_API_KEY not set — add it to .env",
        }

    language = (getattr(settings, "NEWSDATA_API_LANGUAGE", "en") or "en").strip()
    country = (getattr(settings, "NEWSDATA_API_COUNTRY", "") or "").strip()
    categories = list(getattr(settings, "NEWSDATA_API_CATEGORIES", []) or [])
    page_size = _page_size()
    max_requests = max(1, int(getattr(settings, "NEWSDATA_API_MAX_REQUESTS_PER_RUN", 3)))
    fetch_pages = bool(getattr(settings, "NEWSDATA_API_FETCH_ARTICLE_PAGES", False))
    ua = settings.SCRAPER_USER_AGENT

    items, requests_used, errors = _collect_items(
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
            "source": "newsdata",
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
        url = (item.get("link") or item.get("url") or "").strip()
        if storage.exists_url(url):
            skipped += 1
            continue

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

        sk = source_key_for_article_url(url)
        doc = build_article_document(
            canonical_url=url,
            source_key=sk,
            extracted=extracted,
            http_status=http_status,
            content_type=content_type,
            extra={
                "newsdata_article_id": item.get("article_id"),
                "ingestion_channel": "newsdata_api",
                "newsdata_language": item.get("language") or language,
                "newsdata_country": item.get("country"),
                "newsdata_source_name": item.get("source_name"),
                "newsdata_categories": item.get("category"),
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
        "source": "newsdata",
        "api_requests": requests_used,
        "candidates": len(items),
    }
    if errors:
        result["warnings"] = errors
    return result
