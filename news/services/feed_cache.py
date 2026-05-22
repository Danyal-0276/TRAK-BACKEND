"""Short-lived in-memory cache for explore page 1 (dev/single-worker friendly)."""
from __future__ import annotations

import time
from typing import Any, Optional

_EXPLORE_TTL_SEC = 90
_store: dict[str, tuple[float, dict[str, Any]]] = {}


def explore_cache_key(*, limit: int, q: str, cursor: Optional[str]) -> str:
    return f"explore:{limit}:{q}:{cursor or ''}"


def get_cached_explore(key: str) -> Optional[dict[str, Any]]:
    entry = _store.get(key)
    if not entry:
        return None
    ts, data = entry
    if time.time() - ts > _EXPLORE_TTL_SEC:
        _store.pop(key, None)
        return None
    return data


def set_cached_explore(key: str, data: dict[str, Any]) -> None:
    _store[key] = (time.time(), data)


def invalidate_explore_cache() -> None:
    keys = [k for k in _store if k.startswith("explore:")]
    for k in keys:
        _store.pop(k, None)
