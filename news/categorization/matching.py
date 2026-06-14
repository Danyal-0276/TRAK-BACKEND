"""Article ↔ user interest matching (ML categories + semantic keywords + legacy rules)."""

from __future__ import annotations

from django.conf import settings

from news.categorization.embeddings import keyword_matches_embedding
from news.categorization.labels import category_slug, main_category_slugs
from news.platform_taxonomy import DEFAULT_TAGS_WITH_SUBCATEGORIES

from news import category_matching as _legacy


def browse_primary_only_enabled() -> bool:
    return _browse_primary_only()


def _browse_primary_only() -> bool:
    raw = str(getattr(settings, "CATEGORY_BROWSE_PRIMARY_ONLY", "false")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _secondary_min_confidence() -> float:
    try:
        return float(getattr(settings, "CATEGORY_SECONDARY_MIN_CONFIDENCE", 0.38))
    except (TypeError, ValueError):
        return 0.38


def _rule_fallback_enabled() -> bool:
    raw = str(getattr(settings, "CATEGORY_RULE_FALLBACK_ENABLED", "true")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _legacy_interest_matches_hay(keyword: str, hay: str) -> bool:
    if not hay:
        return False
    return any(term in hay for term in _legacy.expand_interest_terms(keyword))


def article_has_ml_category(doc: dict) -> bool:
    return bool(doc.get("primary_category") or doc.get("categories"))


def article_category_slugs(doc: dict, *, primary_only: bool = False) -> set[str]:
    slugs: set[str] = set()
    primary = category_slug(doc.get("primary_category") or "")
    if primary:
        slugs.add(primary)
    if primary_only:
        return slugs
    for raw in doc.get("categories") or []:
        slug = category_slug(raw)
        if slug:
            slugs.add(slug)
    return slugs


def article_browse_slugs(doc: dict) -> set[str]:
    """Category slugs used for browse pages and category badge counts."""
    if not article_has_ml_category(doc):
        return set()

    primary = category_slug(doc.get("primary_category") or "")
    if _browse_primary_only():
        return {primary} if primary else set()

    slugs: set[str] = set()
    if primary:
        slugs.add(primary)

    scores = doc.get("category_scores") or {}
    secondary_min = _secondary_min_confidence()
    has_scores = isinstance(scores, dict) and bool(scores)

    for raw in doc.get("categories") or []:
        slug = category_slug(raw)
        if not slug or slug == primary:
            continue
        if has_scores:
            if float(scores.get(slug, 0) or 0) >= secondary_min:
                slugs.add(slug)
        else:
            slugs.add(slug)
    return slugs


def _legacy_article_matches_category(doc: dict, category_name: str) -> bool:
    key = category_slug(category_name)
    if not key:
        return True
    hay = _legacy.article_haystack(doc)
    display = key.replace("-", " ")
    if display in hay or key in hay:
        return True
    for syn in _legacy.CATEGORY_SYNONYMS.get(key, ()):
        if _legacy.legacy_term_in_hay(syn, hay):
            return True
    for sub in DEFAULT_TAGS_WITH_SUBCATEGORIES.get(key, ()):
        sub_phrase = sub.replace("-", " ")
        if _legacy.legacy_term_in_hay(sub_phrase, hay) or _legacy.legacy_term_in_hay(sub, hay):
            return True
    return False


def _slug_matches_interest(slugs: set[str], key: str) -> bool:
    if key in slugs:
        return True
    for sub in DEFAULT_TAGS_WITH_SUBCATEGORIES.get(key, ()):
        if sub in slugs:
            return True
    return False


def article_matches_category(doc: dict, category_name: str) -> bool:
    """Browse/category filter: all ML labels when multi-category browse is enabled."""
    if not category_name:
        return True
    key = category_slug(category_name)
    if not key:
        return True

    if article_has_ml_category(doc):
        slugs = article_browse_slugs(doc)
        if slugs:
            return _slug_matches_interest(slugs, key)
        return False

    if _rule_fallback_enabled():
        return _legacy_article_matches_category(doc, key)
    return False


def _keyword_matches_ml_category(doc: dict, keyword: str) -> bool:
    key = category_slug(keyword)
    if not key:
        return False
    slugs = article_browse_slugs(doc)
    if not slugs:
        slugs = article_category_slugs(doc)
    if not slugs:
        return False
    if _slug_matches_interest(slugs, key):
        return True
    if key in main_category_slugs():
        return key in slugs
    for parent, subs in DEFAULT_TAGS_WITH_SUBCATEGORIES.items():
        if key in subs and parent in slugs:
            return True
    return False


def interest_matches_article(doc: dict, keyword: str) -> bool:
    """
    User keyword / alert matching:
    1) ML category labels when keyword is a platform category
    2) Semantic embedding similarity for custom keywords
    3) Legacy substring rules
    """
    if not keyword:
        return False

    if _keyword_matches_ml_category(doc, keyword):
        return True

    embedding = doc.get("match_embedding") or []
    if isinstance(embedding, list) and embedding:
        if keyword_matches_embedding(keyword, embedding):
            return True

    if _rule_fallback_enabled():
        hay = _legacy.article_haystack(doc)
        return _legacy_interest_matches_hay(keyword, hay)
    return False


def interest_matches_hay(keyword: str, hay: str) -> bool:
    """Backward-compatible haystack-only match (legacy callers without full doc)."""
    if _rule_fallback_enabled():
        return _legacy_interest_matches_hay(keyword, hay)
    return False
