"""Map model class probabilities to a 0–100 credibility (trustworthiness) score."""

from __future__ import annotations

from typing import Any

def resynthesize_probs_from_peak(label: int, peak: float, n: int = 3) -> list[float]:
    """Build a 3-class distribution from verdict + confidence (peak on winning class)."""
    peak_f = max(0.01, min(0.99, float(peak)))
    probs = [0.0] * n
    idx = max(0, min(n - 1, int(label)))
    probs[idx] = peak_f
    rest = (1.0 - peak_f) / max(1, n - 1)
    for i in range(n):
        if i != idx:
            probs[i] = rest
    return probs


def _is_template_distribution(probs: list[float], label: int) -> bool:
    if len(probs) < 3:
        return False
    try:
        p = [float(probs[0]), float(probs[1]), float(probs[2])]
    except (TypeError, ValueError):
        return False
    idx = max(0, min(2, int(label)))
    if abs(p[idx] - 0.75) > 0.02:
        return False
    for i in range(3):
        if i == idx:
            continue
        if abs(p[i] - 0.125) > 0.02:
            return False
    return True


def _probs_have_variation(probs: list[float]) -> bool:
    """True when distribution is not a fixed synthetic template."""
    if len(probs) < 2:
        return False
    try:
        vals = [float(p) for p in probs[:3]]
    except (TypeError, ValueError):
        return False
    if len(vals) >= 3 and max(vals) - min(vals) < 0.08:
        return False
    return True


def effective_credibility_probs(doc: dict[str, Any]) -> list[float] | None:
    """
    Prefer real model distributions; rebuild from label + max_prob only for legacy template triples.
    """
    label = doc.get("credibility_label")
    peak = doc.get("credibility_max_prob")
    if peak is None:
        peak = doc.get("fake_detection_max_prob")

    raw = doc.get("credibility_probs")
    ml = doc.get("fake_detection_probs")

    for candidate in (raw, ml):
        if not isinstance(candidate, list) or len(candidate) < 2:
            continue
        try:
            probs = [float(x) for x in candidate[:3]]
        except (TypeError, ValueError):
            continue
        while len(probs) < 3:
            probs.append(max(0.0, 1.0 - sum(probs)))
        if label is not None and _is_template_distribution(probs, int(label)):
            continue
        if _probs_have_variation(probs):
            total = sum(probs)
            if total > 0 and abs(total - 1.0) > 0.02:
                probs = [p / total for p in probs]
            return probs

    if label is None:
        if not isinstance(raw, list) or not raw:
            return None
        return [float(x) for x in raw]

    if peak is not None:
        try:
            peak_f = float(peak)
        except (TypeError, ValueError):
            peak_f = None
        if peak_f is not None and peak_f > 0:
            if not isinstance(raw, list) or len(raw) < 2:
                return resynthesize_probs_from_peak(int(label), peak_f)
            if _is_template_distribution(list(raw), int(label)):
                return resynthesize_probs_from_peak(int(label), peak_f)

    if isinstance(raw, list) and raw:
        try:
            return [float(x) for x in raw]
        except (TypeError, ValueError):
            return None
    return None


def compute_credibility_score(probs: Any) -> int | None:
    """
    Higher = more trustworthy.
    Net: P(real) - P(fake) - 0.25 * P(suspicious) → 0–100.
    """
    if not isinstance(probs, list) or not probs:
        return None
    try:
        p_real = float(probs[0]) if len(probs) > 0 else 0.0
        p_fake = float(probs[1]) if len(probs) > 1 else 0.0
        p_susp = float(probs[2]) if len(probs) > 2 else 0.0
    except (TypeError, ValueError):
        return None

    if len(probs) == 2:
        net = p_real - p_fake
    else:
        net = p_real - p_fake - 0.25 * p_susp

    net = max(-1.0, min(1.0, net))
    return int(round(50 + 50 * net))


def compute_credibility_score_from_doc(doc: dict[str, Any]) -> int | None:
    probs = effective_credibility_probs(doc)
    if probs:
        return compute_credibility_score(probs)
    return None


def verdict_confidence_percent(doc: dict[str, Any]) -> int | None:
    """Confidence in the assigned verdict (0–100), from max_prob or label slot."""
    label = doc.get("credibility_label")
    probs = effective_credibility_probs(doc)
    if label is not None and probs and 0 <= int(label) < len(probs):
        try:
            return int(round(float(probs[int(label)]) * 100))
        except (TypeError, ValueError):
            pass
    for key in ("credibility_max_prob", "fake_detection_max_prob"):
        val = doc.get(key)
        if val is not None:
            try:
                p = float(val)
                if p > 0:
                    return int(round((p if p <= 1 else p / 100) * 100))
            except (TypeError, ValueError):
                continue
    return None


def score_style_tier(score: int | None) -> str:
    if score is None:
        return "unknown"
    if score >= 70:
        return "real"
    if score >= 40:
        return "suspicious"
    return "fake"


def prob_breakdown(probs: list[float] | None) -> dict[str, int] | None:
    if not probs or len(probs) < 3:
        return None
    try:
        return {
            "real": int(round(float(probs[0]) * 100)),
            "fake": int(round(float(probs[1]) * 100)),
            "suspicious": int(round(float(probs[2]) * 100)),
        }
    except (TypeError, ValueError):
        return None
