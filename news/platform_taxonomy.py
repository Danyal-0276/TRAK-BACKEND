"""Platform categories (onboarding tags) and connections stored in MongoDB admin settings."""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from news.mongo_db import user_preferences_collection

DEFAULT_TAGS_WITH_SUBCATEGORIES: dict[str, list[str]] = {
    "politics": ["elections", "government", "policy", "international-relations", "campaigns"],
    "business": ["stocks", "markets", "startups", "economy", "trade", "corporate"],
    "technology": ["ai", "smartphones", "software", "cybersecurity", "innovation", "gadgets"],
    "sports": ["football", "basketball", "soccer", "baseball", "olympics", "tennis"],
    "entertainment": ["movies", "tv-shows", "celebrity", "music", "streaming", "awards"],
    "health": ["medicine", "fitness", "nutrition", "mental-health", "research", "wellness"],
    "science": ["research", "space", "climate", "biology", "physics", "discoveries"],
    "world-news": ["international", "conflicts", "diplomacy", "global-events"],
    "local-news": ["community", "city-council", "local-events", "neighborhood"],
    "breaking-news": ["alerts", "urgent", "live-updates", "emergency"],
    "finance": ["banking", "investments", "cryptocurrency", "personal-finance", "markets"],
    "weather": ["forecast", "storms", "climate-change", "seasonal"],
    "education": ["schools", "universities", "students", "teachers", "learning"],
    "lifestyle": ["fashion", "home", "relationships", "self-improvement", "trends"],
    "food": ["recipes", "restaurants", "cooking", "nutrition", "food-culture"],
    "travel": ["destinations", "airlines", "hotels", "tourism", "adventure"],
    "automotive": ["cars", "electric-vehicles", "reviews", "industry-news"],
    "real-estate": ["housing", "market-trends", "buying", "selling", "rentals"],
    "opinion": ["editorials", "op-eds", "commentary", "analysis"],
    "culture": ["arts", "literature", "traditions", "society", "heritage"],
    "environment": ["climate-change", "conservation", "pollution", "sustainability"],
    "crime": ["investigations", "court-cases", "law-enforcement", "safety"],
    "military": ["defense", "veterans", "conflicts", "security"],
    "gaming": ["video-games", "esports", "reviews", "industry", "streaming"],
    "startup": ["funding", "unicorns", "innovation", "entrepreneurs", "tech-companies"],
    "social-media": ["platforms", "influencers", "trends", "digital-culture"],
}

ADMIN_SCOPE = "admin_settings"


def _slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return s.strip("-") or str(uuid.uuid4())[:8]


def _display_name(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _normalize_subcategory(raw: Any, parent_slug: str) -> dict[str, Any] | None:
    if isinstance(raw, str):
        slug = _slugify(raw)
        if not slug:
            return None
        return {"id": slug, "slug": slug, "name": _display_name(slug)}
    if isinstance(raw, dict):
        slug = _slugify(raw.get("slug") or raw.get("id") or raw.get("name") or "")
        if not slug:
            return None
        name = str(raw.get("name") or "").strip() or _display_name(slug)
        return {"id": slug, "slug": slug, "name": name, "parent_slug": parent_slug}
    return None


def _normalize_category(raw: Any, *, sort_order: int = 0) -> dict[str, Any] | None:
    if isinstance(raw, str):
        slug = _slugify(raw)
        if not slug:
            return None
        return {
            "id": slug,
            "slug": slug,
            "name": _display_name(slug),
            "subcategories": [],
            "sort_order": sort_order,
            "active": True,
        }
    if isinstance(raw, dict):
        slug = _slugify(raw.get("slug") or raw.get("id") or raw.get("name") or "")
        if not slug:
            return None
        name = str(raw.get("name") or "").strip() or _display_name(slug)
        subs_raw = raw.get("subcategories") or []
        subs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sub in subs_raw:
            norm = _normalize_subcategory(sub, slug)
            if norm and norm["slug"] not in seen:
                seen.add(norm["slug"])
                subs.append(norm)
        return {
            "id": slug,
            "slug": slug,
            "name": name,
            "subcategories": subs,
            "sort_order": int(raw.get("sort_order", sort_order)),
            "active": bool(raw.get("active", True)),
        }
    return None


def _normalize_connection(raw: Any, *, sort_order: int = 0) -> dict[str, Any] | None:
    if isinstance(raw, str):
        slug = _slugify(raw)
        if not slug:
            return None
        return {
            "id": slug,
            "slug": slug,
            "name": _display_name(slug),
            "url": "",
            "sort_order": sort_order,
            "active": True,
        }
    if isinstance(raw, dict):
        slug = _slugify(raw.get("slug") or raw.get("id") or raw.get("name") or "")
        if not slug:
            return None
        name = str(raw.get("name") or "").strip() or _display_name(slug)
        kind = str(raw.get("kind") or "rss").strip().lower() or "rss"
        scraper_module = str(raw.get("scraper_module") or "").strip() or None
        source_key = str(raw.get("source_key") or "").strip() or None
        row = {
            "id": slug,
            "slug": slug,
            "name": name,
            "url": str(raw.get("url") or "").strip(),
            "kind": kind,
            "sort_order": int(raw.get("sort_order", sort_order)),
            "active": bool(raw.get("active", True)),
        }
        if scraper_module:
            row["scraper_module"] = scraper_module
        if source_key:
            row["source_key"] = source_key
        return row
    return None


def _default_categories() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, (main, subs) in enumerate(DEFAULT_TAGS_WITH_SUBCATEGORIES.items()):
        cat = _normalize_category(
            {
                "slug": main,
                "name": _display_name(main),
                "subcategories": subs,
                "sort_order": idx,
            }
        )
        if cat:
            out.append(cat)
    return out


def _get_settings_row() -> dict[str, Any]:
    return user_preferences_collection().find_one({"scope": ADMIN_SCOPE}) or {}


def _save_categories_connections(
    categories: list[dict[str, Any]],
    connections: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    user_preferences_collection().update_one(
        {"scope": ADMIN_SCOPE},
        {
            "$set": {
                "categories": categories,
                "connections": connections,
                "taxonomy_updated_at": now,
            },
            "$setOnInsert": {"scope": ADMIN_SCOPE, "created_at": now},
        },
        upsert=True,
    )
    return _get_settings_row()


def seed_taxonomy_if_empty() -> bool:
    row = _get_settings_row()
    cats = row.get("categories") or []
    if cats:
        return False
    defaults = _default_categories()
    conns = list_connections(raw=row.get("connections"))
    if not conns:
        from news.scrape_sources import default_connections_from_catalog

        conns = default_connections_from_catalog()
    _save_categories_connections(defaults, conns)
    return True


def seed_connections_if_empty() -> bool:
    """Seed scrape sources into connections when the list is empty."""
    row = _get_settings_row()
    if row.get("connections"):
        return False
    from news.scrape_sources import default_connections_from_catalog

    categories = list_categories(seed=False) or _default_categories()
    _save_categories_connections(categories, default_connections_from_catalog())
    return True


def merge_catalog_connections() -> int:
    """Add catalog scrape sources missing from admin connections (by URL or slug)."""
    from news.scrape_sources import default_connections_from_catalog

    existing = list_connections()
    by_url = {(c.get("url") or "").strip() for c in existing if (c.get("url") or "").strip()}
    by_slug = {c["slug"] for c in existing}
    added = 0
    merged = list(existing)
    for row in default_connections_from_catalog():
        url = (row.get("url") or "").strip()
        slug = row["slug"]
        if slug in by_slug:
            continue
        if url and url in by_url:
            continue
        merged.append(row)
        by_slug.add(slug)
        if url:
            by_url.add(url)
        added += 1
    if added:
        _persist_connections(merged)
    return added


def list_categories(*, raw: list | None = None, seed: bool = True) -> list[dict[str, Any]]:
    if seed:
        seed_taxonomy_if_empty()
    row = _get_settings_row()
    source = raw if raw is not None else (row.get("categories") or [])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(source):
        norm = _normalize_category(item, sort_order=idx)
        if norm and norm["slug"] not in seen:
            seen.add(norm["slug"])
            out.append(norm)
    if not out and seed:
        out = _default_categories()
        _save_categories_connections(out, list_connections(raw=row.get("connections")))
    out.sort(key=lambda c: (c.get("sort_order", 0), c.get("name", "")))
    return out


def list_connections(*, raw: list | None = None, seed: bool = True) -> list[dict[str, Any]]:
    if raw is None and seed:
        seed_connections_if_empty()
    row = _get_settings_row()
    source = raw if raw is not None else (row.get("connections") or [])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(source):
        norm = _normalize_connection(item, sort_order=idx)
        if norm and norm["slug"] not in seen:
            seen.add(norm["slug"])
            out.append(norm)
    out.sort(key=lambda c: (c.get("sort_order", 0), c.get("name", "")))
    return out


def tags_with_subcategories_map(categories: list[dict[str, Any]] | None = None) -> dict[str, list[str]]:
    cats = categories if categories is not None else list_categories()
    result: dict[str, list[str]] = {}
    for cat in cats:
        if not cat.get("active", True):
            continue
        slug = cat["slug"]
        subs = [
            s["slug"]
            for s in (cat.get("subcategories") or [])
            if s.get("slug")
        ]
        result[slug] = subs
    return result


def get_public_taxonomy() -> dict[str, Any]:
    categories = list_categories()
    connections = [c for c in list_connections() if c.get("active", True)]
    return {
        "tags_with_subcategories": tags_with_subcategories_map(categories),
        "categories": categories,
        "connections": connections,
    }


def _persist_categories(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    row = _get_settings_row()
    connections = list_connections(raw=row.get("connections"))
    _save_categories_connections(categories, connections)
    return list_categories(seed=False)


def _persist_connections(connections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    row = _get_settings_row()
    categories = list_categories(seed=False)
    _save_categories_connections(categories, connections)
    return list_connections()


def create_category(name: str, subcategories: list[str] | None = None) -> dict[str, Any]:
    slug = _slugify(name)
    if not slug:
        raise ValueError("Category name is required.")
    categories = list_categories(seed=False)
    if any(c["slug"] == slug for c in categories):
        raise ValueError("Category already exists.")
    subs = []
    for sub_name in subcategories or []:
        norm = _normalize_subcategory(sub_name, slug)
        if norm:
            subs.append(norm)
    categories.append(
        {
            "id": slug,
            "slug": slug,
            "name": str(name).strip() or _display_name(slug),
            "subcategories": subs,
            "sort_order": len(categories),
            "active": True,
        }
    )
    _persist_categories(categories)
    return next(c for c in list_categories(seed=False) if c["slug"] == slug)


def update_category(slug: str, *, name: str | None = None, active: bool | None = None) -> dict[str, Any]:
    key = _slugify(slug)
    categories = list_categories(seed=False)
    found = None
    for cat in categories:
        if cat["slug"] != key:
            continue
        if name is not None:
            cat["name"] = str(name).strip() or cat["name"]
        if active is not None:
            cat["active"] = bool(active)
        found = cat
        break
    if not found:
        raise ValueError("Category not found.")
    _persist_categories(categories)
    return found


def delete_category(slug: str) -> None:
    key = _slugify(slug)
    categories = [c for c in list_categories(seed=False) if c["slug"] != key]
    if len(categories) == len(list_categories(seed=False)):
        raise ValueError("Category not found.")
    _persist_categories(categories)


def add_subcategory(category_slug: str, name: str) -> dict[str, Any]:
    key = _slugify(category_slug)
    sub = _normalize_subcategory(name, key)
    if not sub:
        raise ValueError("Subcategory name is required.")
    categories = list_categories(seed=False)
    found = None
    for cat in categories:
        if cat["slug"] != key:
            continue
        if any(s["slug"] == sub["slug"] for s in cat.get("subcategories") or []):
            raise ValueError("Subcategory already exists.")
        cat.setdefault("subcategories", []).append(sub)
        found = sub
        break
    if not found:
        raise ValueError("Category not found.")
    _persist_categories(categories)
    return found


def update_subcategory(category_slug: str, sub_slug: str, *, name: str) -> dict[str, Any]:
    cat_key = _slugify(category_slug)
    sub_key = _slugify(sub_slug)
    categories = list_categories(seed=False)
    found = None
    for cat in categories:
        if cat["slug"] != cat_key:
            continue
        for sub in cat.get("subcategories") or []:
            if sub["slug"] != sub_key:
                continue
            sub["name"] = str(name).strip() or sub["name"]
            found = sub
            break
    if not found:
        raise ValueError("Subcategory not found.")
    _persist_categories(categories)
    return found


def delete_subcategory(category_slug: str, sub_slug: str) -> None:
    cat_key = _slugify(category_slug)
    sub_key = _slugify(sub_slug)
    categories = list_categories(seed=False)
    changed = False
    for cat in categories:
        if cat["slug"] != cat_key:
            continue
        before = len(cat.get("subcategories") or [])
        cat["subcategories"] = [s for s in (cat.get("subcategories") or []) if s["slug"] != sub_key]
        changed = len(cat["subcategories"]) < before
    if not changed:
        raise ValueError("Subcategory not found.")
    _persist_categories(categories)


def create_connection(name: str, url: str = "", *, kind: str = "rss") -> dict[str, Any]:
    slug = _slugify(name)
    if not slug:
        raise ValueError("Connection name is required.")
    url = str(url or "").strip()
    kind = str(kind or "rss").strip().lower() or "rss"
    if kind == "rss" and not url:
        raise ValueError("URL is required for RSS scrape sources.")
    connections = list_connections()
    if any(c["slug"] == slug for c in connections):
        raise ValueError("Connection already exists.")
    if url and any((c.get("url") or "").strip() == url for c in connections):
        raise ValueError("A connection with this URL already exists.")
    row: dict[str, Any] = {
        "id": slug,
        "slug": slug,
        "name": str(name).strip() or _display_name(slug),
        "url": url,
        "kind": kind,
        "sort_order": len(connections),
        "active": True,
    }
    connections.append(row)
    _persist_connections(connections)
    return next(c for c in list_connections() if c["slug"] == slug)


def update_connection(slug: str, *, name: str | None = None, url: str | None = None, active: bool | None = None) -> dict[str, Any]:
    key = _slugify(slug)
    connections = list_connections()
    found = None
    for conn in connections:
        if conn["slug"] != key:
            continue
        if name is not None:
            conn["name"] = str(name).strip() or conn["name"]
        if url is not None:
            conn["url"] = str(url).strip()
        if active is not None:
            conn["active"] = bool(active)
        found = conn
        break
    if not found:
        raise ValueError("Connection not found.")
    _persist_connections(connections)
    return found


def delete_connection(slug: str) -> None:
    key = _slugify(slug)
    connections = [c for c in list_connections() if c["slug"] != key]
    if len(connections) == len(list_connections()):
        raise ValueError("Connection not found.")
    _persist_connections(connections)


def replace_categories(categories_payload: list) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(categories_payload):
        norm = _normalize_category(item, sort_order=idx)
        if norm and norm["slug"] not in seen:
            seen.add(norm["slug"])
            normalized.append(norm)
    _persist_categories(normalized)
    return list_categories(seed=False)


def replace_connections(connections_payload: list) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, item in enumerate(connections_payload):
        norm = _normalize_connection(item, sort_order=idx)
        if norm and norm["slug"] not in seen:
            seen.add(norm["slug"])
            normalized.append(norm)
    _persist_connections(normalized)
    return list_connections()
