"""Currents API (currentsapi.services) — global news JSON feed."""

from __future__ import annotations

import json
import re
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

BASE_URL = "https://api.currentsapi.services/v1"


def _api_key() -> str:
    return (getattr(settings, "CURRENTS_API_KEY", "") or "").strip()


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": _api_key(),
        "Accept": "application/json",
    }


def _parse_currents_published(raw: str | None):
    if not raw:
        return None
    s = raw.strip().replace(" ", "T", 1)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    return parse_published_datetime(s)


def _normalize_image(image: str | None) -> str | None:
    if not image:
        return None
    img = str(image).strip()
    if not img or img.lower() in ("none", "null"):
        return None
    if img.startswith("//"):
        return "https:" + img
    return img


def _extracted_from_api_item(item: dict[str, Any]) -> dict[str, Any] | None:
    url = (item.get("url") or "").strip()
    title = normalize_ws(item.get("title") or "")
    description = normalize_ws(item.get("description") or "")
    if not url or not title:
        return None

    categories = item.get("category") or item.get("source_category") or []
    if isinstance(categories, list):
        category = categories[0] if categories else None
    else:
        category = str(categories) if categories else None

    author = (item.get("author") or "").strip() or None
    body = description or title

    return {
        "title": title,
        "summary": description[:500] if description else None,
        "body_text": body,
        "published_at": _parse_currents_published(item.get("published")),
        "author_name": author,
        "category": category,
        "image_url": _normalize_image(item.get("image")),
    }


def _fetch_news(
    client: PoliteHttpClient,
    path: str,
    params: dict[str, str],
) -> tuple[list[dict[str, Any]], str | None]:
    query = urlencode({k: v for k, v in params.items() if v})
    url = f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"
    try:
        response = client.get(url, extra_headers=_auth_headers())
    except Exception as exc:
        return [], str(exc)
    if response.status_code == 429:
        return [], "daily request limit reached (429)"
    if response.status_code == 401:
        return [], "invalid CURRENTS_API_KEY (401)"
    if response.status_code != 200:
        return [], f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return [], "invalid JSON response"
    if payload.get("status") != "ok":
        return [], str(payload.get("message") or payload.get("status") or "API error")
    news = payload.get("news") or []
    return [n for n in news if isinstance(n, dict)], None


def _collect_items(
    client: PoliteHttpClient,
    *,
    language: str,
    country: str,
    categories: list[str],
    max_requests: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    requests_used = 0

    def add_batch(batch: list[dict[str, Any]]) -> None:
        for item in batch:
            key = str(item.get("id") or item.get("url") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(item)

    if requests_used < max_requests:
        if country:
            params: dict[str, str] = {"language": language, "country": country}
            label = f"search/country={country}"
            path = "/search"
        else:
            params = {"language": language}
            label = "latest-news"
            path = "/latest-news"
        batch, err = _fetch_news(client, path, params)
        requests_used += 1
        if err:
            errors.append(f"{label}: {err}")
        else:
            add_batch(batch)

    for category in categories:
        if requests_used >= max_requests:
            errors.append("stopped early: CURRENTS_API_MAX_REQUESTS_PER_RUN reached")
            break
        search_params: dict[str, str] = {"language": language, "category": category}
        if country:
            search_params["country"] = country
        batch, err = _fetch_news(
            client,
            "/search",
            search_params,
        )
        requests_used += 1
        if err:
            errors.append(f"search/{category}: {err}")
        else:
            add_batch(batch)

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
            "source": "currents",
            "note": "CURRENTS_API_KEY not set — add it to .env",
        }

    language = (getattr(settings, "CURRENTS_API_LANGUAGE", "en") or "en").strip()
    country = (getattr(settings, "CURRENTS_API_COUNTRY", "") or "").strip().upper()
    categories = list(getattr(settings, "CURRENTS_API_SEARCH_CATEGORIES", []) or [])
    max_requests = max(1, int(getattr(settings, "CURRENTS_API_MAX_REQUESTS_PER_RUN", 5)))
    fetch_pages = bool(getattr(settings, "CURRENTS_API_FETCH_ARTICLE_PAGES", False))
    ua = settings.SCRAPER_USER_AGENT

    items, requests_used, errors = _collect_items(
        client,
        language=language,
        country=country,
        categories=categories,
        max_requests=max_requests,
    )
    if not items and errors:
        return {
            "inserted": 0,
            "skipped": 0,
            "source": "currents",
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
            if getattr(settings, "SCRAPER_STORE_RAW_HTML", False) and page_extracted:
                raw_html = None

        sk = source_key_for_article_url(url)
        doc = build_article_document(
            canonical_url=url,
            source_key=sk,
            extracted=extracted,
            http_status=http_status,
            content_type=content_type,
            extra={
                "currents_id": item.get("id"),
                "ingestion_channel": "currents_api",
                "currents_language": item.get("language") or language,
                "currents_categories": item.get("category") or item.get("source_category"),
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
        "source": "currents",
        "api_requests": requests_used,
        "candidates": len(items),
    }
    if errors:
        result["warnings"] = errors
    return result
