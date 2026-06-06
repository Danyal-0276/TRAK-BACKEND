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


def _trim_to_word_boundary(text: str, max_len: int) -> str:
    s = _normalize_line(text)
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    cut = s[:max_len]
    last_space = cut.rfind(" ")
    if last_space > int(max_len * 0.6):
        cut = cut[:last_space]
    return f"{cut.rstrip()}…"


def _strip_title_prefix(text: str, title: str) -> str:
    s = _normalize_line(text)
    t = _normalize_line(title)
    if not s or not t:
        return s
    if s.lower().startswith(t.lower()) and len(s) > len(t) + 15:
        return re.sub(r"^[\s\-–—:|]+", "", s[len(t) :]).strip()
    return s


def _first_usable_body_line(body: str, *, title: str = "", min_len: int = 40) -> str:
    cleaned = sanitize_article_body(body, title=title)
    if not cleaned:
        return ""
    t = _normalize_line(title).lower()
    for block in re.split(r"\n\s*\n+", cleaned):
        line = _normalize_line(block)
        if len(line) < min_len or is_nav_boilerplate_line(line):
            continue
        if t and line.lower() == t:
            continue
        if t and line.lower().startswith(t) and len(line) <= len(t) + 20:
            continue
        return line
    for block in re.split(r"\n\s*\n+", cleaned):
        line = _normalize_line(block)
        if not line or is_nav_boilerplate_line(line):
            continue
        if t and line.lower() == t:
            continue
        return line
    return ""


def build_card_summary(
    *,
    title: str = "",
    stored_summary: str = "",
    body: str = "",
    max_len: int = 500,
) -> str:
    """Feed-card blurb: pipeline summary with body fallbacks when missing or headline-only."""
    full_text = sanitize_article_body(body, title=title)
    summary = sanitize_article_summary(stored_summary, title=title, body=full_text)
    if summary:
        return _trim_to_word_boundary(summary, max_len)

    if not full_text:
        return ""

    parts = re.split(r"(?<=[.!?])\s+", full_text.strip())
    candidate = " ".join(parts[:2]) if parts else full_text[:400]
    summary = sanitize_article_summary(candidate, title=title, body=full_text)
    if summary:
        return _trim_to_word_boundary(summary, max_len)

    line = _first_usable_body_line(full_text, title=title)
    if line:
        return _trim_to_word_boundary(line, max_len)

    snippet = _strip_title_prefix(full_text, title)
    if len(snippet) >= 30 and not is_nav_boilerplate_line(snippet):
        return _trim_to_word_boundary(snippet, max_len)

    return ""
