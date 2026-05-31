"""Article image URL helpers — URLs only (no binary storage in MongoDB)."""

from __future__ import annotations

from typing import Any


def article_image_url(doc: dict[str, Any] | None) -> str | None:
    if not doc:
        return None
    for key in ("image_url", "image", "thumbnail_url"):
        val = doc.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return None


def hydrate_processed_image_urls(docs: list[dict[str, Any]], raw_col) -> None:
    """Fill missing image_url on processed docs from matching raw_articles (in-place)."""
    if not docs or raw_col is None:
        return

    missing_canonical: list[str] = []
    for doc in docs:
        if article_image_url(doc):
            continue
        canon = doc.get("canonical_url") or doc.get("raw_canonical_url")
        if canon:
            missing_canonical.append(canon)

    if not missing_canonical:
        return

    by_canonical: dict[str, str] = {}
    for raw in raw_col.find(
        {"canonical_url": {"$in": list(set(missing_canonical))}},
        {"canonical_url": 1, "image_url": 1, "image": 1, "thumbnail_url": 1},
    ):
        img = article_image_url(raw)
        canon = raw.get("canonical_url")
        if img and canon:
            by_canonical[str(canon)] = img

    for doc in docs:
        if article_image_url(doc):
            continue
        canon = str(doc.get("canonical_url") or doc.get("raw_canonical_url") or "")
        if canon and canon in by_canonical:
            doc["image_url"] = by_canonical[canon]
