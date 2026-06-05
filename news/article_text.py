"""Strip navigation menus and breadcrumb junk from scraped article text."""

from __future__ import annotations

import re

NAV_MENU_TOKENS = frozenset({
    "latest",
    "home",
    "world",
    "sports",
    "business",
    "health",
    "entertainment",
    "showbiz",
    "pakistan",
    "royal",
    "opinion",
    "videos",
    "video",
    "photos",
    "photo",
    "contact",
    "about",
    "menu",
    "search",
    "login",
    "subscribe",
    "trending",
    "national",
    "international",
    "sci-tech",
    "technology",
    "crime",
    "lifestyle",
})


def _normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", (line or "").strip())


def is_nav_boilerplate_line(line: str) -> bool:
    s = _normalize_line(line)
    if not s:
        return True
    low = s.lower()
    if low in NAV_MENU_TOKENS:
        return True
    words = low.split()
    if len(words) <= 4 and all(w in NAV_MENU_TOKENS for w in words):
        return True
    if len(s) < 28 and low == s.lower() and " " not in s:
        return True
    return False


def sanitize_article_body(text: str, *, title: str = "") -> str:
    """Remove site nav / breadcrumb paragraphs from article body."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    title_norm = _normalize_line(title).lower()
    kept: list[str] = []
    for block in re.split(r"\n\s*\n+", raw):
        line = _normalize_line(block)
        if not line:
            continue
        if is_nav_boilerplate_line(line):
            continue
        if title_norm and line.lower() == title_norm:
            continue
        if title_norm and line.lower().startswith(title_norm) and len(line) <= len(title_norm) + 12:
            continue
        kept.append(line)
    return "\n\n".join(kept).strip()


def sanitize_article_summary(summary: str, *, title: str = "", body: str = "") -> str:
    """Avoid card summaries that repeat the headline or end with nav crumbs like 'Home'."""
    s = _normalize_line(summary)
    t = _normalize_line(title)
    if not s:
        return s
    s = re.sub(r"\s+Home\s*$", "", s, flags=re.I).strip()
    if t and (s.lower() == t.lower() or s.lower().startswith(t.lower())):
        cleaned = sanitize_article_body(body, title=title)
        for block in re.split(r"\n\s*\n+", cleaned):
            line = _normalize_line(block)
            if len(line) >= 60 and not is_nav_boilerplate_line(line):
                if t.lower() not in line.lower()[: max(20, len(t))]:
                    return line[:500]
                if len(line) > len(t) + 40:
                    return line[:500]
        return ""
    return s[:10000]
