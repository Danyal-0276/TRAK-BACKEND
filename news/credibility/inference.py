"""
3-class credibility pipeline:
  1) Fake detection via HF Space (FAKE_DETECTION_SPACE_ID) or local HF weights (CREDIBILITY_MODEL_PATH)
  2) Second pass: Google Fact Check API (FACT_CHECKER_ENABLED)
  3) Merge → final credibility_label (0=real, 1=fake, 2=suspicious)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from django.conf import settings

from news.credibility.combine import combine_credibility
from news.factcheck.service import preload_fact_checker, verify_claim
from news.spaces.client import parse_classification_response, preload_space, space_predict

logger = logging.getLogger(__name__)

_DEFAULT_ID2LABEL = {0: "real", 1: "fake", 2: "suspicious"}

_model = None
_tokenizer = None
_device = None
_labels_map: dict[int, str] = dict(_DEFAULT_ID2LABEL)
_model_path_loaded: str = ""


def _mongo_safe_labels_map(src: dict[Any, Any]) -> dict[str, str]:
    return {str(k): str(v) for k, v in dict(src).items()}


def _fake_detection_space_id() -> str:
    try:
        return (getattr(settings, "FAKE_DETECTION_SPACE_ID", None) or "").strip()
    except Exception:
        return (os.environ.get("FAKE_DETECTION_SPACE_ID") or "").strip()


def _fake_detection_api_name() -> Optional[str]:
    try:
        name = (getattr(settings, "FAKE_DETECTION_SPACE_API_NAME", None) or "").strip()
    except Exception:
        name = (os.environ.get("FAKE_DETECTION_SPACE_API_NAME") or "").strip()
    return name or None


def _pick_device():
    try:
        import torch
    except ImportError:
        return None
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _labels_from_model_config(model) -> dict[int, str]:
    cfg = getattr(model, "config", None)
    raw = getattr(cfg, "id2label", None) if cfg is not None else None
    if not raw:
        return dict(_DEFAULT_ID2LABEL)
    out: dict[int, str] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    return out if len(out) >= 2 else dict(_DEFAULT_ID2LABEL)


def _read_metadata_threshold(model_dir: str) -> Optional[float]:
    path = os.path.join(model_dir, "metadata.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        t = meta.get("confidence_threshold")
        if t is None:
            return None
        return float(t)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _load_hf_local() -> bool:
    global _model, _tokenizer, _device, _labels_map, _model_path_loaded
    path = getattr(settings, "CREDIBILITY_MODEL_PATH", None) or ""
    if not path or not os.path.isdir(path):
        return False
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch
    except ImportError:
        logger.warning("transformers/torch not installed; skipping local credibility model")
        return False
    try:
        _tokenizer = AutoTokenizer.from_pretrained(path)
        _model = AutoModelForSequenceClassification.from_pretrained(path)
        _device = _pick_device()
        if _device is not None:
            _model = _model.to(_device)
        _model.eval()
        _labels_map = _labels_from_model_config(_model)
        _model_path_loaded = path
        logger.info("Credibility model loaded from %s", path)
        return True
    except Exception as e:
        logger.exception("Failed to load local credibility model: %s", e)
        _model = None
        _tokenizer = None
        _device = None
        _labels_map = dict(_DEFAULT_ID2LABEL)
        _model_path_loaded = ""
        return False


def _predict_fake_detection_space(text: str) -> Optional[dict[str, Any]]:
    space_id = _fake_detection_space_id()
    if not space_id:
        return None
    try:
        raw = space_predict(space_id, text[:8000], api_name=_fake_detection_api_name())
    except Exception as exc:
        logger.exception("Fake detection Space failed (%s): %s", space_id, exc)
        return None

    parsed = parse_classification_response(raw)
    probs = parsed["probs"]
    if not probs:
        from news.credibility.score import resynthesize_probs_from_peak

        n = 3
        idx = min(int(parsed["label_id"]), n - 1)
        peak = float(parsed["max_prob"] or 0.0)
        if peak <= 0:
            peak = 0.55
        probs = resynthesize_probs_from_peak(idx, peak, n=n)

    idx = min(int(parsed["label_id"]), len(probs) - 1)
    peak_prob = float(probs[idx]) if probs else float(parsed["max_prob"] or 0.0)
    return {
        "fake_detection_label": parsed["label_id"],
        "fake_detection_probs": probs,
        "fake_detection_max_prob": peak_prob,
        "fake_detection_model_id": f"hf-space:{space_id}",
        "fake_detection_labels_map": _mongo_safe_labels_map(_DEFAULT_ID2LABEL),
    }


def _predict_fake_detection_local(text: str) -> Optional[dict[str, Any]]:
    if _model is None:
        _load_hf_local()
    if _model is None or _tokenizer is None:
        return None
    try:
        import torch

        threshold = _effective_threshold()
        inputs = _tokenizer(
            text[:8000],
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        if _device is not None:
            inputs = {k: v.to(_device) for k, v in inputs.items()}
        with torch.no_grad():
            logits = _model(**inputs).logits[0]
            probs = torch.softmax(logits, dim=-1).tolist()
        pred = int(max(range(len(probs)), key=lambda i: probs[i]))
        max_prob = float(max(probs))
        label = pred if max_prob >= threshold else 2
        return {
            "fake_detection_label": label,
            "fake_detection_probs": probs,
            "fake_detection_max_prob": max_prob,
            "fake_detection_model_id": _model_path_loaded,
            "fake_detection_labels_map": _mongo_safe_labels_map(_labels_map),
        }
    except Exception as e:
        logger.exception("Local fake detection inference error: %s", e)
        return None


def _stub_fake_fraction(text: str) -> float:
    """
    Heuristic fake-likelihood in [0.05, 0.92] — varies per article (unlike fixed 0.28 fake mass).
    Used only when HF Space and local model are unavailable.
    """
    import hashlib
    import re

    t = (text or "").lower()
    words = re.findall(r"[a-z0-9]{3,}", t)
    n_words = len(words)

    fake_markers = (
        "breaking:",
        "shocking",
        "miracle",
        "secret",
        "hoax",
        "conspiracy",
        "click here",
        "you won",
        "guaranteed",
        "doctors hate",
        "they don't want",
        "100%",
        "free money",
        "cures all",
        "overnight",
    )
    news_markers = (
        "said",
        "according to",
        "official",
        "minister",
        "government",
        "report",
        "match",
        "final",
        "bid",
        "announced",
        "approved",
    )
    hits = sum(1 for m in fake_markers if m in t)
    news_hits = sum(1 for m in news_markers if m in t)
    exclaims = text.count("!")
    caps = sum(1 for c in text if c.isupper())
    caps_ratio = caps / max(1, len(text))

    fake_frac = 0.14
    fake_frac += 0.09 * min(4, hits)
    fake_frac += 0.04 * min(4, exclaims)
    fake_frac += 0.14 * min(1.0, caps_ratio * 12.0)
    fake_frac -= 0.05 * min(5, news_hits)
    if n_words < 60:
        fake_frac += 0.14
    elif n_words > 120:
        fake_frac -= 0.06
    elif n_words > 350:
        fake_frac -= 0.1

    # Stable per-article spread so admin scores are not identical when content differs.
    digest = hashlib.sha256(t[:4000].encode("utf-8", errors="ignore")).digest()
    jitter = (int.from_bytes(digest[:4], "big") / (2**32) - 0.5) * 0.14
    fake_frac += jitter

    return max(0.05, min(0.92, fake_frac))


def _predict_fake_detection_stub(text: str) -> dict[str, Any]:
    threshold = _effective_threshold()
    fake_frac = _stub_fake_fraction(text)
    real_frac = max(0.05, 1.0 - fake_frac)
    # Uncertainty highest when fake/real are balanced.
    balance = 1.0 - abs(fake_frac - 0.5) * 2.0
    susp_frac = max(0.05, min(0.35, 0.08 + 0.22 * balance))
    scale = 1.0 - susp_frac
    probs = [real_frac * scale, fake_frac * scale, susp_frac]
    total = sum(probs)
    probs = [p / total for p in probs]
    pred = int(max(range(len(probs)), key=lambda i: probs[i]))
    # Keep argmax label; combine_credibility applies threshold for final verdict.
    return {
        "fake_detection_label": pred,
        "fake_detection_probs": probs,
        "fake_detection_max_prob": float(probs[pred]),
        "fake_detection_model_id": "stub-heuristic",
        "fake_detection_labels_map": _mongo_safe_labels_map(_DEFAULT_ID2LABEL),
    }


def _run_fake_detection(text: str) -> dict[str, Any]:
    space_result = _predict_fake_detection_space(text)
    if space_result:
        return space_result
    local_result = _predict_fake_detection_local(text)
    if local_result:
        return local_result
    if _fake_detection_space_id():
        logger.warning(
            "Fake detection Space %s configured but unavailable; using stub-heuristic (install gradio-client, check HF_TOKEN)",
            _fake_detection_space_id(),
        )
    return _predict_fake_detection_stub(text)


def preload_credibility_model() -> dict[str, Any]:
    """Warm up fake-detection Space/local model and fact-check config."""
    space_id = _fake_detection_space_id()
    fact = preload_fact_checker()

    if space_id:
        space_info = preload_space(space_id)
        return {
            "mode": "space" if space_info.get("loaded") else "space-unavailable",
            "loaded": bool(space_info.get("loaded")),
            "space_id": space_id,
            "fact_checker": fact,
        }

    path = getattr(settings, "CREDIBILITY_MODEL_PATH", None) or ""
    if path:
        ok = _load_hf_local() if _model is None else True
        return {
            "mode": "hf-local" if ok and _model is not None else "stub",
            "loaded": bool(ok and _model is not None),
            "path": path,
            "fact_checker": fact,
        }

    return {"mode": "stub", "loaded": False, "reason": "FAKE_DETECTION_SPACE_ID unset", "fact_checker": fact}


def _effective_threshold() -> float:
    try:
        path = getattr(settings, "CREDIBILITY_MODEL_PATH", None) or ""
        if path and os.path.isdir(path):
            meta_t = _read_metadata_threshold(path)
            if meta_t is not None:
                return meta_t
        return float(getattr(settings, "CREDIBILITY_CONFIDENCE_THRESHOLD", 0.6))
    except Exception:
        return float(os.environ.get("CREDIBILITY_CONFIDENCE_THRESHOLD", 0.6))


def predict_credibility(text: str, *, title: str = "") -> dict[str, Any]:
    """
    Fake detection (Space) → Google Fact Check API → merged credibility_* fields.
    """
    text = (text or "").strip()
    labels_out = _mongo_safe_labels_map(_DEFAULT_ID2LABEL)
    if not text:
        empty_ml = {
            "fake_detection_label": 2,
            "fake_detection_probs": [0.0, 0.0, 1.0],
            "fake_detection_max_prob": 1.0,
            "fake_detection_model_id": "empty-text",
            "fake_detection_labels_map": labels_out,
        }
        empty_fc = verify_claim("", title=title, ml_label=2)
        return combine_credibility(empty_ml, empty_fc)

    ml = _run_fake_detection(text)
    fc = verify_claim(text, title=title, ml_label=int(ml.get("fake_detection_label", 2)))
    return combine_credibility(ml, fc)
