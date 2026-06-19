"""Explore feed response cache (Redis when USE_REDIS=true, else Django locmem)."""

from __future__ import annotations

import time
from typing import Any, Optional

from django.core.cache import cache

_CACHE_VERSION_KEY = "trak:feed:explore:version"
_EXPLORE_TTL_PAGE1 = 120
_EXPLORE_TTL_PAGE_N = 90


def _cache_version() -> int:
    return int(cache.get(_CACHE_VERSION_KEY) or 1)


def explore_cache_key(*, limit: int, q: str, cursor: Optional[str], category: str = "") -> str:
    cat = (category or "").strip().lower()
    return f"trak:feed:explore:{_cache_version()}:{limit}:{q}:{cat}:{cursor or ''}"


def get_cached_explore(key: str) -> Optional[dict[str, Any]]:
    data = cache.get(key)
    return data if isinstance(data, dict) else None


def set_cached_explore(key: str, data: dict[str, Any], *, cursor: Optional[str] = None) -> None:
    ttl = _EXPLORE_TTL_PAGE1 if not cursor else _EXPLORE_TTL_PAGE_N
    cache.set(key, data, timeout=ttl)


_CATEGORY_COUNTS_KEY = "trak:feed:category_counts"
_CATEGORY_COUNTS_TTL = 600


def get_cached_category_counts() -> Optional[dict[str, int]]:
    data = cache.get(_CATEGORY_COUNTS_KEY)
    if not isinstance(data, dict):
        return None
    return {str(k): int(v) for k, v in data.items()}


def set_cached_category_counts(counts: dict[str, int]) -> None:
    cache.set(_CATEGORY_COUNTS_KEY, counts, timeout=_CATEGORY_COUNTS_TTL)


def invalidate_category_counts_cache() -> None:
    cache.delete(_CATEGORY_COUNTS_KEY)


def invalidate_explore_cache() -> None:
    cache.set(_CACHE_VERSION_KEY, int(time.time()), timeout=None)
    invalidate_category_counts_cache()
