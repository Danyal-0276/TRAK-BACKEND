"""English stopwords from spaCy + NLTK (lazy-loaded, cached)."""

from __future__ import annotations

# News/speech verbs often capitalized at sentence start — not entities
_ENTITY_EXTRA = frozenset(
    """
    after before while since although however moreover furthermore therefore meanwhile
    according said says told ask asked add added note noted report reported speak spoke
    write wrote announce announced confirm confirmed deny denied claim claimed state stated
    reveal revealed warn warned urge urged
    """.split()
)

_CACHED: frozenset[str] | None = None


def _load_nltk_stopwords() -> set[str]:
    import nltk
    from nltk.corpus import stopwords as nltk_stopwords

    try:
        nltk_stopwords.words("english")
    except LookupError:
        nltk.download("stopwords", quiet=True)
    return {w.lower() for w in nltk_stopwords.words("english")}


def get_english_stopwords() -> frozenset[str]:
    """Merged stopword set: spaCy EN + NLTK EN + small news-entity extras."""
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    words: set[str] = set(_ENTITY_EXTRA)

    try:
        from spacy.lang.en.stop_words import STOP_WORDS

        words.update(w.lower() for w in STOP_WORDS)
    except Exception:
        pass

    try:
        words.update(_load_nltk_stopwords())
    except Exception:
        pass

    if len(words) < 50:
        # Minimal fallback if libraries missing
        words.update(
            "the a an and or but in on at to for of is was are been be have has had "
            "it its with from by about into than then also just only very more most".split()
        )

    _CACHED = frozenset(words)
    return _CACHED
