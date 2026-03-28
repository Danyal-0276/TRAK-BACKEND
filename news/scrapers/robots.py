"""Respect robots.txt before fetching URLs."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

_cache: dict[str, RobotFileParser] = {}


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

    rp = _cache.get(base)
    if rp is None:
        rp = RobotFileParser()
        rp.set_url(robots_url)
        try:
            rp.read()
            _cache[base] = rp
        except Exception:
            # Unknown policy — do not crawl without explicit permission
            return False

    try:
        return rp.can_fetch(user_agent, url)
    except Exception:
        return False
