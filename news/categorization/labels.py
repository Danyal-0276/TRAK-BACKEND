"""Platform category labels for zero-shot classification."""

from __future__ import annotations

from news.platform_taxonomy import DEFAULT_TAGS_WITH_SUBCATEGORIES, list_categories


def _display_name(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def category_slug(raw: str) -> str:
    return str(raw or "").strip().lower().replace(" ", "-")


def main_category_slugs() -> frozenset[str]:
    try:
        cats = list_categories(seed=False)
        if cats:
            return frozenset(c["slug"] for c in cats if c.get("active", True))
    except Exception:
        pass
    return frozenset(DEFAULT_TAGS_WITH_SUBCATEGORIES.keys())


def zero_shot_candidate_labels() -> tuple[list[str], dict[str, str]]:
    """
    Returns (display_labels, display_label -> slug).
    Zero-shot works better with readable phrases than raw slugs.
    """
    try:
        cats = list_categories(seed=False)
    except Exception:
        cats = []
    if not cats:
        cats = [
            {"slug": slug, "name": _display_name(slug), "active": True}
            for slug in DEFAULT_TAGS_WITH_SUBCATEGORIES.keys()
        ]
    display: list[str] = []
    to_slug: dict[str, str] = {}
    seen: set[str] = set()
    for cat in cats:
        if not cat.get("active", True):
            continue
        slug = category_slug(cat.get("slug") or cat.get("name") or "")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        label = str(cat.get("name") or "").strip() or _display_name(slug)
        display.append(label)
        to_slug[label] = slug
    return display, to_slug
