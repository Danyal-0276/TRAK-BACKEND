"""Merge fake-detection (ML Space) + external fact-check API into final credibility fields."""

from __future__ import annotations

import os
from typing import Any

from django.conf import settings

from news.credibility.score import compute_credibility_score_from_doc

_DEFAULT_ID2LABEL = {0: "real", 1: "fake", 2: "suspicious"}


def _mongo_safe_labels_map(src: dict[Any, Any]) -> dict[str, str]:
    return {str(k): str(v) for k, v in dict(src).items()}


def combine_credibility(
    ml: dict[str, Any],
    fact_check: dict[str, Any],
) -> dict[str, Any]:
    """
    Produce final credibility_* fields stored on processed_articles.

    ml keys: fake_detection_label, fake_detection_probs, fake_detection_max_prob, fake_detection_model_id
    fact_check keys: fact_check_* from verify_claim()
    """
    try:
        threshold = float(getattr(settings, "CREDIBILITY_CONFIDENCE_THRESHOLD", 0.6))
    except Exception:
        threshold = float(os.environ.get("CREDIBILITY_CONFIDENCE_THRESHOLD", 0.6))
    labels_map = ml.get("fake_detection_labels_map") or _DEFAULT_ID2LABEL
    ml_label = int(ml.get("fake_detection_label", 2))
    ml_probs = list(ml.get("fake_detection_probs") or [])
    ml_max = float(ml.get("fake_detection_max_prob") or 0.0)
    ml_model = str(ml.get("fake_detection_model_id") or "unknown")

    final_label = ml_label
    final_probs = ml_probs if ml_probs else [0.0, 0.0, 1.0]
    final_max = ml_max if ml_max > 0 else max(final_probs) if final_probs else 0.0

    verdict = fact_check.get("fact_check_verdict") or "skipped"
    suggested = fact_check.get("fact_check_suggested_label")

    if verdict == "contradicts_ml" and suggested is not None:
        # External fact-check disagrees with ML → escalate toward fact-check label
        final_label = int(suggested)
        if final_probs and len(final_probs) > final_label:
            final_max = float(final_probs[final_label])
        else:
            final_max = max(final_max, 0.7)
    elif verdict == "supports_ml" and suggested is not None:
        final_label = int(suggested)
        if final_probs and len(final_probs) > final_label:
            final_max = float(final_probs[final_label])
    elif verdict == "mixed":
        final_label = 2
    elif verdict == "no_hits" and ml_max < threshold:
        final_label = 2

    if final_max < threshold:
        final_label = 2

    if not final_probs:
        final_probs = [0.0, 0.0, 1.0]
    elif len(final_probs) == 2:
        rem = max(0.0, 1.0 - float(final_probs[0]) - float(final_probs[1]))
        final_probs = [float(final_probs[0]), float(final_probs[1]), rem]
    while len(final_probs) < 3:
        final_probs.append(max(0.0, 1.0 - sum(float(p) for p in final_probs)))

    total = sum(float(p) for p in final_probs)
    if total > 0 and abs(total - 1.0) > 0.02:
        final_probs = [float(p) / total for p in final_probs]

    # Keep max_prob aligned with the chosen label (not argmax from another class).
    if final_probs and 0 <= int(final_label) < len(final_probs):
        idx = int(final_label)
        if float(final_probs[idx]) <= 0 and idx == 2 and len(final_probs) >= 3:
            rem = max(0.0, 1.0 - float(final_probs[0]) - float(final_probs[1]))
            final_probs[2] = max(0.25, rem)
            total = sum(final_probs)
            final_probs = [float(p) / total for p in final_probs]
        final_max = float(final_probs[idx])

    cred_score = compute_credibility_score_from_doc(
        {"credibility_label": final_label, "credibility_probs": final_probs, "credibility_max_prob": final_max}
    )

    return {
        "fake_detection_label": ml_label,
        "fake_detection_probs": ml_probs,
        "fake_detection_max_prob": ml_max,
        "fake_detection_model_id": ml_model,
        "fake_detection_labels_map": _mongo_safe_labels_map(labels_map),
        **fact_check,
        "credibility_label": final_label,
        "credibility_probs": final_probs,
        "credibility_max_prob": final_max,
        "credibility_score": cred_score,
        "credibility_model_id": f"{ml_model}+factcheck",
        "credibility_labels_map": _mongo_safe_labels_map(labels_map),
    }
