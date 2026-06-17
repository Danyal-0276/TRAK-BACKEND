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


def explore_cache_key(*, limit: int, q: str, cursor: Optional[str]) -> str:
    return f"trak:feed:explore:{_cache_version()}:{limit}:{q}:{cursor or ''}"


def get_cached_explore(key: str) -> Optional[dict[str, Any]]:
    data = cache.get(key)
    return data if isinstance(data, dict) else None


def set_cached_explore(key: str, data: dict[str, Any], *, cursor: Optional[str] = None) -> None:
    ttl = _EXPLORE_TTL_PAGE1 if not cursor else _EXPLORE_TTL_PAGE_N
    cache.set(key, data, timeout=ttl)


def invalidate_explore_cache() -> None:
    cache.set(_CACHE_VERSION_KEY, int(time.time()), timeout=None)
