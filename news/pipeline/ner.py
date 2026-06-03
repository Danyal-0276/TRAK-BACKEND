"""
Named-entity extraction for processed articles.

Uses spaCy NER (en_core_web_sm) when the model is installed; otherwise a
heuristic with the same stopword filtering (spaCy + NLTK stop word lists).
"""

from __future__ import annotations

import os
import re
from typing import Any

from django.conf import settings

from news.pipeline.stopwords import get_english_stopwords

# spaCy labels we keep (people, places, orgs, events — not dates/numbers)
_SPACY_ENTITY_LABELS = frozenset(
    {"PERSON", "ORG", "GPE", "LOC", "FAC", "NORP", "EVENT", "PRODUCT"}
)

_SPACY_NLP: Any = None
_SPACY_LOAD_ATTEMPTED = False
_SPACY_MODEL_ID = "heuristic-v2"


def ner_model_id() -> str:
    _get_spacy_nlp()
    return _SPACY_MODEL_ID


def _stopwords() -> frozenset[str]:
    return get_english_stopwords()


def _normalize_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _is_junk_entity(text: str) -> bool:
    stops = _stopwords()
    t = _normalize_phrase(text)
    if len(t) < 2:
        return True
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", t)]
    if not words:
        return True
    if all(w in stops for w in words):
        return True
    if len(words) == 1 and words[0] in stops:
        return True
    if sum(c.isalpha() for c in t) < 2:
        return True
    return False


def _trim_leading_stops(phrase: str) -> str:
    stops = _stopwords()
    words = phrase.split()
    while words and words[0].lower() in stops:
        words.pop(0)
    return " ".join(words)


def _get_spacy_nlp():
    global _SPACY_NLP, _SPACY_LOAD_ATTEMPTED, _SPACY_MODEL_ID
    if _SPACY_LOAD_ATTEMPTED:
        return _SPACY_NLP
    _SPACY_LOAD_ATTEMPTED = True
    try:
        import spacy

        model = (getattr(settings, "SPACY_MODEL", None) or "en_core_web_sm").strip()
        _SPACY_NLP = spacy.load(model, disable=["lemmatizer"])
        _SPACY_MODEL_ID = f"spacy-{model}"
    except Exception:
        _SPACY_NLP = None
    return _SPACY_NLP


def _extract_spacy(text: str, *, max_chars: int = 12000, max_entities: int = 20) -> list[dict[str, Any]]:
    nlp = _get_spacy_nlp()
    if nlp is None:
        return []
    doc = nlp(text[:max_chars])
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for ent in doc.ents:
        if ent.label_ not in _SPACY_ENTITY_LABELS:
            continue
        phrase = _trim_leading_stops(_normalize_phrase(ent.text))
        if _is_junk_entity(phrase):
            continue
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": phrase, "label": ent.label_})
        if len(out) >= max_entities:
            break
    return out


def _count_occurrences(needle: str, haystack: str) -> int:
    if not needle:
        return 0
    return len(re.findall(r"\b" + re.escape(needle) + r"\b", haystack, re.IGNORECASE))


def _extract_heuristic(
    text: str,
    *,
    title: str = "",
    max_chars: int = 8000,
    max_entities: int = 16,
) -> list[dict[str, Any]]:
    stops = _stopwords()
    src = text[:max_chars]
    title_blob = f" {title} "
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def add(phrase: str, label: str) -> None:
        phrase = _trim_leading_stops(_normalize_phrase(phrase))
        if _is_junk_entity(phrase):
            return
        key = phrase.lower()
        if key in seen:
            return
        seen.add(key)
        out.append({"text": phrase, "label": label})

    for m in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b", src):
        add(m.group(0), "MISC")

    for m in re.finditer(r"\b[A-Z][a-z]{2,}\b", src):
        word = m.group(0)
        if word.lower() in stops:
            continue
        in_title = word.lower() in title_blob.lower()
        repeated = _count_occurrences(word, src) >= 2
        if in_title or repeated:
            add(word, "MISC")

    for m in re.finditer(r"\b[A-Z]{2,6}\b", src):
        tok = m.group(0)
        if tok.lower() in stops:
            continue
        add(tok, "ORG")

    return out[:max_entities]


def extract_entities(
    text: str,
    *,
    title: str = "",
    max_chars: int = 12000,
    max_entities: int = 20,
) -> list[dict[str, Any]]:
    """
    Return [{text, label}, ...] for people, places, organizations, and similar.
    """
    blob = _normalize_phrase(f"{title}\n{text}")
    if not blob:
        return []

    spacy_out = _extract_spacy(blob, max_chars=max_chars, max_entities=max_entities)
    if spacy_out:
        return spacy_out

    return _extract_heuristic(
        blob,
        title=title,
        max_chars=max_chars,
        max_entities=max_entities,
    )
