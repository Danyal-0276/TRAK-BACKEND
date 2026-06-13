"""Match user category/keyword interests against processed articles (feed + alerts)."""

from __future__ import annotations

import re

from news.platform_taxonomy import DEFAULT_TAGS_WITH_SUBCATEGORIES

# Align with web `categoryMatch.js` — substring terms that imply a category.
CATEGORY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "politics": ("politics", "political", "election", "government", "parliament", "congress", "minister"),
    "business": ("business", "economy", "market", "stock", "finance", "trade", "company", "corporate", "ceo"),
    "technology": (
        "technology",
        "tech",
        "software",
        "artificial intelligence",
        "digital",
        "cyber",
        "startup",
    ),
    "sports": ("sports", "sport", "football", "soccer", "nba", "cricket", "olympic", "league", "tennis"),
    "entertainment": ("entertainment", "celebrity", "movie", "music", "hollywood", "netflix", "streaming"),
    "health": ("health", "medical", "medicine", "hospital", "doctor", "wellness", "disease", "vaccine"),
    "science": ("science", "scientific", "research", "space", "nasa", "physics", "biology", "discovery"),
    "world-news": ("world", "international", "global", "foreign", "diplomacy", "ukraine", "gaza", "nato"),
    "local-news": ("local", "community", "city", "county", "neighborhood"),
    "breaking-news": ("breaking", "urgent", "alert", "live updates"),
    "finance": ("finance", "banking", "investment", "cryptocurrency", "bitcoin", "inflation", "fed"),
    "weather": ("weather", "storm", "hurricane", "flood", "forecast"),
    "education": ("education", "school", "university", "student", "teacher", "college"),
    "lifestyle": ("lifestyle", "fashion", "relationship"),
    "food": ("food", "restaurant", "recipe", "cooking", "chef"),
    "travel": ("travel", "tourism", "airline", "hotel", "vacation"),
    "automotive": ("automotive", "car", "vehicle", "electric vehicle", "tesla"),
    "real-estate": ("real estate", "housing", "mortgage", "rent", "property"),
    "opinion": ("opinion", "editorial", "commentary", "op-ed"),
    "culture": ("culture", "art", "literature", "heritage", "museum"),
    "environment": ("environment", "climate", "pollution", "carbon", "renewable", "conservation", "wildlife"),
    "crime": ("crime", "police", "court", "murder", "arrest", "prison", "fbi"),
    "military": ("military", "army", "defense", "pentagon", "veteran", "weapon"),
    "gaming": ("gaming", "video game", "esports", "playstation", "xbox"),
    "startup": ("startup", "unicorn", "venture", "funding", "entrepreneur"),
    "social-media": ("social media", "instagram", "tiktok", "facebook", "twitter", "influencer"),
}

MAIN_CATEGORY_SLUGS = frozenset(DEFAULT_TAGS_WITH_SUBCATEGORIES.keys())


def _term_in_hay(term: str, hay: str) -> bool:
    """Substring match; short terms use word boundaries to avoid false positives (e.g. tech in technical)."""
    term = str(term or "").strip().lower()
    if not term or not hay:
        return False
    if " " in term or len(term) > 5:
        return term in hay
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", hay))


def _slug_variants(term: str) -> set[str]:
    base = str(term or "").strip().lower()
    if not base:
        return set()
    slug = base.replace(" ", "-")
    spaced = base.replace("-", " ")
    return {base, slug, spaced}


def expand_interest_terms(keyword: str) -> set[str]:
    """All substrings to test for one saved category or custom keyword."""
    terms: set[str] = set()
    for v in _slug_variants(keyword):
        terms.add(v)
        if v in DEFAULT_TAGS_WITH_SUBCATEGORIES:
            for sub in DEFAULT_TAGS_WITH_SUBCATEGORIES[v]:
                terms.update(_slug_variants(sub))
            terms.update(CATEGORY_SYNONYMS.get(v, ()))
        for key, syns in CATEGORY_SYNONYMS.items():
            if v == key or v.replace("-", " ") == key.replace("-", " "):
                terms.update(syns)
    return {t for t in terms if len(t) >= 2}


def interest_matches_hay(keyword: str, hay: str) -> bool:
    """Legacy haystack-only match; prefer interest_matches_article(doc, keyword)."""
    from news.categorization.matching import interest_matches_hay as _ml_hay

    return _ml_hay(keyword, hay)


def legacy_term_in_hay(term: str, hay: str) -> bool:
    return _term_in_hay(term, hay)


def count_main_categories_selected(keywords: list[str]) -> int:
    count = 0
    seen: set[str] = set()
    for raw in keywords:
        for v in _slug_variants(raw):
            if v in MAIN_CATEGORY_SLUGS and v not in seen:
                seen.add(v)
                count += 1
    return count


def article_haystack(doc: dict) -> str:
    """Processed article text blob for category matching (aligned with web categoryMatch.js)."""
    parts: list[str] = [
        str(doc.get("title") or ""),
        str(doc.get("summary") or ""),
        str(doc.get("clean_text") or "")[:4000],
        str(doc.get("normalized_text") or "")[:2000],
        str(doc.get("source_key") or ""),
    ]
    for k in doc.get("topic_keywords") or []:
        parts.append(str(k))
    for t in doc.get("normalized_terms") or []:
        parts.append(str(t))
    for e in doc.get("entities") or []:
        if isinstance(e, dict):
            parts.append(str(e.get("text") or ""))
    return " ".join(parts).lower()


def article_matches_category(doc: dict, category_name: str) -> bool:
    """True when a processed article belongs to a browse category slug/name."""
    from news.categorization.matching import article_matches_category as _ml_match

    return _ml_match(doc, category_name)


def interest_matches_article(doc: dict, keyword: str) -> bool:
    """Match a user keyword against a processed article (ML + semantic + legacy rules)."""
    from news.categorization.matching import interest_matches_article as _ml_match

    return _ml_match(doc, keyword)


def user_follows_all_categories(keywords: list[str]) -> bool:
    """True when the user selected essentially every platform category (broad alerts)."""
    if not keywords:
        return False
    main_count = count_main_categories_selected(keywords)
    total_main = len(MAIN_CATEGORY_SLUGS)
    if total_main == 0:
        return False
    if main_count >= max(18, total_main - 2):
        return True
    # Many distinct interests (main + subs) ≈ “select all” in onboarding.
    return len({str(k).strip().lower() for k in keywords if str(k).strip()}) >= 45
