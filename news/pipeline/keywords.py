"""Derive topic keywords for personalization (stored on processed_articles)."""

from __future__ import annotations

import re
from typing import Any

from news.pipeline.stopwords import get_english_stopwords


def extract_topic_keywords(
    cleaned: str,
    title: str,
    summary: str,
    entities: list[dict[str, Any]],
    *,
    max_tokens: int = 40,
) -> list[str]:
    """
    Multi-source keywords: significant tokens from title/summary/body prefix
    plus NER-style entity strings. Lowercase, deduped, capped length.
    """
    stops = get_english_stopwords()
    blob = f"{title} {summary} {cleaned[:2000]}".lower()
    words = re.findall(r"[a-z][a-z0-9-]{2,}", blob)
    out: list[str] = []
    for w in words:
        if w in stops:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= max_tokens:
            break
    for e in entities:
        t = str(e.get("text", "")).strip().lower()
        t = re.sub(r"\s+", " ", t)
        if len(t) < 3 or t in stops or t in out:
            continue
        parts = [p for p in t.split() if p and p not in stops]
        if not parts:
            continue
        if len(parts) > 1:
            out.append(" ".join(parts))
        elif parts[0] not in out:
            out.append(parts[0])
    return out[: max_tokens + 16]
