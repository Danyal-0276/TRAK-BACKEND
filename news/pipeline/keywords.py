"""Derive topic keywords for personalization (stored on processed_articles)."""

from __future__ import annotations

import re
from typing import Any

# Light stopword list — articles/blogs often share these; keeps tokens topical
_STOP = frozenset(
    "the a an and or but in on at to for of as is was are were been be have has had "
    "do does did will would could should may might must not no yes this that these those "
    "it its with from by about into than then also just only very more most some any each "
    "all can one two we you they who which what when where why how if so such than".split()
)


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
    blob = f"{title} {summary} {cleaned[:2000]}".lower()
    words = re.findall(r"[a-z][a-z0-9-]{2,}", blob)
    out: list[str] = []
    for w in words:
        if w in _STOP:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= max_tokens:
            break
    for e in entities:
        t = str(e.get("text", "")).strip().lower()
        t = re.sub(r"\s+", " ", t)
        if len(t) > 2 and t not in out and t not in _STOP:
            parts = t.split()
            if len(parts) > 1:
                out.append(t)
            elif parts:
                w = parts[0]
                if w not in _STOP and w not in out:
                    out.append(w)
    return out[: max_tokens + 16]
