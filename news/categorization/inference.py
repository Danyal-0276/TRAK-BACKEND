"""Zero-shot news category classification (Hugging Face MNLI models)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from django.conf import settings

from news.categorization.labels import category_slug, main_category_slugs, zero_shot_candidate_labels

logger = logging.getLogger(__name__)

_classifier = None
_classifier_model_id = ""
_classifier_lock = threading.Lock()


def _default_model_id() -> str:
    return (
        getattr(settings, "CATEGORY_CLASSIFIER_MODEL", None)
        or "typeform/distilbert-base-uncased-mnli"
    ).strip()


def _enabled() -> bool:
    raw = str(getattr(settings, "CATEGORY_CLASSIFIER_ENABLED", "true")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _confidence_threshold() -> float:
    try:
        return float(getattr(settings, "CATEGORY_CONFIDENCE_THRESHOLD", 0.28))
    except (TypeError, ValueError):
        return 0.28


def _primary_min_confidence() -> float:
    try:
        return float(getattr(settings, "CATEGORY_PRIMARY_MIN_CONFIDENCE", 0.35))
    except (TypeError, ValueError):
        return 0.35


def _secondary_min_confidence() -> float:
    try:
        return float(getattr(settings, "CATEGORY_SECONDARY_MIN_CONFIDENCE", 0.38))
    except (TypeError, ValueError):
        return 0.38


def _browse_primary_only() -> bool:
    raw = str(getattr(settings, "CATEGORY_BROWSE_PRIMARY_ONLY", "false")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _max_labels() -> int:
    try:
        return max(1, min(8, int(getattr(settings, "CATEGORY_MAX_LABELS", 3))))
    except (TypeError, ValueError):
        return 3


def _hypothesis_template() -> str:
    return (
        getattr(settings, "CATEGORY_HYPOTHESIS_TEMPLATE", None)
        or "This news article is about {}."
    ).strip()


def _max_input_chars() -> int:
    try:
        return max(128, int(getattr(settings, "CATEGORY_CLASSIFIER_MAX_CHARS", 2000)))
    except (TypeError, ValueError):
        return 2000


def _secondary_relative_min() -> float:
    try:
        return max(0.5, min(0.95, float(getattr(settings, "CATEGORY_SECONDARY_RELATIVE_MIN", 0.72))))
    except (TypeError, ValueError):
        return 0.72


def _secondary_threshold(primary_score: float) -> float:
    """Secondary labels must clear both the floor and a fraction of the primary score."""
    return max(_secondary_min_confidence(), primary_score * _secondary_relative_min())


def _valid_main_slugs() -> frozenset[str]:
    return main_category_slugs()


def _get_classifier():
    global _classifier, _classifier_model_id
    if _classifier is not None:
        return _classifier
    with _classifier_lock:
        if _classifier is not None:
            return _classifier
        model_id = _default_model_id()
        try:
            from transformers import pipeline

            _classifier = pipeline(
                "zero-shot-classification",
                model=model_id,
                device=-1,
            )
            _classifier_model_id = model_id
            logger.info("Category zero-shot model loaded: %s", model_id)
        except Exception as exc:
            logger.warning("Category classifier unavailable: %s", exc)
            _classifier = None
            _classifier_model_id = ""
    return _classifier


def category_model_id() -> str:
    if _classifier is not None:
        return _classifier_model_id
    _get_classifier()
    return _classifier_model_id


def preload_category_classifier() -> bool:
    return _get_classifier() is not None


def _classification_input(*, title: str, summary: str, clean_text: str) -> str:
    parts = [str(title or "").strip(), str(summary or "").strip()]
    body = str(clean_text or "").strip()
    if body:
        parts.append(body[:800])
    text = ". ".join(p for p in parts if p)
    return text[: _max_input_chars()]


def predict_categories(
    *,
    title: str = "",
    summary: str = "",
    clean_text: str = "",
) -> dict[str, Any]:
    """
    Classify into TRAK platform categories.
    Returns primary_category, categories[], category_scores, category_confidence, category_model_id.
    """
    empty: dict[str, Any] = {
        "primary_category": "",
        "categories": [],
        "category_scores": {},
        "category_confidence": 0.0,
        "category_model_id": "",
    }
    if not _enabled():
        return empty

    text = _classification_input(title=title, summary=summary, clean_text=clean_text)
    if len(text.strip()) < 20:
        return empty

    clf = _get_classifier()
    if clf is None:
        return empty

    display_labels, label_to_slug = zero_shot_candidate_labels()
    if not display_labels:
        return empty

    try:
        result = clf(
            text,
            candidate_labels=display_labels,
            multi_label=True,
            hypothesis_template=_hypothesis_template(),
        )
    except Exception as exc:
        logger.warning("Category classification failed: %s", exc)
        return empty

    labels_out = result.get("labels") or []
    scores_out = result.get("scores") or []
    threshold = _confidence_threshold()
    max_labels = _max_labels()

    scored: list[tuple[str, float]] = []
    score_map: dict[str, float] = {}
    for label, score in zip(labels_out, scores_out):
        slug = label_to_slug.get(str(label)) or category_slug(str(label))
        if not slug:
            continue
        val = float(score)
        score_map[slug] = val
        if val >= threshold:
            scored.append((slug, val))

    scored.sort(key=lambda x: x[1], reverse=True)
    valid_slugs = _valid_main_slugs()

    if not scored and labels_out and scores_out:
        slug = label_to_slug.get(str(labels_out[0])) or category_slug(str(labels_out[0]))
        top_score = float(scores_out[0]) if scores_out else 0.0
        if slug and slug in valid_slugs and top_score >= _primary_min_confidence():
            scored = [(slug, top_score)]
            score_map[slug] = top_score

    if not scored:
        return empty

    primary_slug, primary_score = scored[0]
    if primary_score < _primary_min_confidence():
        return empty

    secondary_cutoff = _secondary_threshold(primary_score)
    final_categories: list[str] = [primary_slug]
    for slug, val in scored[1:]:
        if slug not in valid_slugs or slug == primary_slug:
            continue
        if val >= secondary_cutoff and len(final_categories) < max_labels:
            final_categories.append(slug)

    return {
        "primary_category": primary_slug,
        "categories": final_categories,
        "category_scores": score_map,
        "category_confidence": primary_score,
        "category_model_id": category_model_id(),
    }
