"""HTTP client with delay, timeout, and shared headers.

Uses curl_cffi with Chrome TLS impersonation so CDN-protected sites (e.g. Cloudflare)
return 200 instead of 403 for Python clients.

A single :class:`Session` is reused across requests on this client to reuse TLS
state and TCP connections where possible (fewer handshakes on long scrapes).
"""

from __future__ import annotations

import time
from typing import Optional
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests
from django.conf import settings


class PoliteHttpClient:
    """Thin wrapper with per-request delay, browser-like TLS, and a reused session."""

    def __init__(self) -> None:
        self._last_request_at: float = 0.0
        self._headers = {
            "User-Agent": settings.SCRAPER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        self._session = curl_requests.Session(impersonate="chrome120")

    def _sleep_if_needed(self) -> None:
        delay = settings.SCRAPER_DELAY_SECONDS
        if delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < delay:
            time.sleep(delay - elapsed)

    def get(
        self,
        url: str,
        *,
        extra_headers: Optional[dict] = None,
    ):
        self._sleep_if_needed()
        headers = {**self._headers, **(extra_headers or {})}
        r = self._session.get(
            url,
            headers=headers,
            timeout=settings.SCRAPER_REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        self._last_request_at = time.monotonic()
        return r

    def close(self) -> None:
        """Release the underlying session (optional; process exit also cleans up)."""
        self._session.close()

    @staticmethod
    def host_for_robots(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
