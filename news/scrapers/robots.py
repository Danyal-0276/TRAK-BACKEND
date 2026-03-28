"""Respect robots.txt before fetching URLs."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

_cache: dict[str, RobotFileParser] = {}
_failed_hosts: set[str] = set()


def _fetch_robots_body(robots_url: str) -> Optional[str]:
    """
    Fallback when urllib's RobotFileParser.read() fails (TLS/CDN quirks).
    Uses the same stack as article fetches so behaviour is consistent.
    """
    try:
        from curl_cffi import requests as curl_requests

        r = curl_requests.get(
            robots_url,
            impersonate="chrome120",
            timeout=20,
            allow_redirects=True,
        )
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    return None


def allowed(url: str, user_agent: str) -> bool:
    """
    Return True if robots.txt allows fetching `url` for this user-agent.
    If robots.txt cannot be loaded, returns False (fail closed for safety).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    base = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = urljoin(base, "/robots.txt")

    if base in _failed_hosts:
        return False

    rp = _cache.get(base)
    if rp is None:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        loaded = False
        try:
            rp.read()
            loaded = True
        except Exception:
            body = _fetch_robots_body(robots_url)
            if body:
                try:
                    rp.parse(body.splitlines())
                    loaded = True
                except Exception:
                    loaded = False
        if not loaded:
            _failed_hosts.add(base)
            return False
        _cache[base] = rp

    try:
        return rp.can_fetch(user_agent, url)
    except Exception:
        return False
