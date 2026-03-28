"""Respect robots.txt before fetching URLs."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

_cache: dict[str, RobotFileParser] = {}
_failed_hosts: set[str] = set()


def _fetch_robots_body(robots_url: str) -> Optional[str]:
    """
    Fetch robots.txt with browser-like TLS (same stack as article requests).
    Many CDNs return 403 to urllib's default client; curl_cffi usually succeeds.
    """
    try:
        from curl_cffi import requests as curl_requests
        from django.conf import settings as dj_settings

        timeout = float(getattr(dj_settings, "SCRAPER_REQUEST_TIMEOUT", 30) or 30)

        r = curl_requests.get(
            robots_url,
            impersonate="chrome120",
            timeout=timeout,
            allow_redirects=True,
        )
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    return None


def _load_robots_parser(robots_url: str) -> Optional[RobotFileParser]:
    """Build a RobotFileParser for this host, or None if robots could not be loaded."""
    body = _fetch_robots_body(robots_url)
    if body:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        try:
            rp.parse(body.splitlines())
            return rp
        except Exception:
            pass

    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        return None
    # urllib returns HTTP 401/403 without raising; read() sets disallow_all and no rules.
    if getattr(rp, "disallow_all", False) and not getattr(rp, "entries", None):
        return None
    return rp


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
        rp = _load_robots_parser(robots_url)
        if rp is None:
            _failed_hosts.add(base)
            return False
        _cache[base] = rp

    try:
        return rp.can_fetch(user_agent, url)
    except Exception:
        return False
