"""
3-class credibility: 0=real, 1=fake, 2=suspicious.
Loads HuggingFace transformers when CREDIBILITY_MODEL_PATH is set; applies softmax
and CREDIBILITY_CONFIDENCE_THRESHOLD → label 2 (suspicious) when max prob is low.

Call preload_credibility_model() from workers (e.g. run_ai_pipeline) to fail fast and
pin device (CPU / CUDA / MPS) at process start.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_DEFAULT_ID2LABEL = {0: "real", 1: "fake", 2: "suspicious"}

_model = None
_tokenizer = None
_device = None
_labels_map: dict[int, str] = dict(_DEFAULT_ID2LABEL)
_model_path_loaded: str = ""


def _mongo_safe_labels_map(src: dict[Any, Any]) -> dict[str, str]:
    return {str(k): str(v) for k, v in dict(src).items()}


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


def _load_hf() -> bool:
    global _model, _tokenizer, _device, _labels_map, _model_path_loaded
    path = getattr(settings, "CREDIBILITY_MODEL_PATH", None) or ""
    if not path or not os.path.isdir(path):
        return False
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch
    except ImportError:
        logger.warning("transformers/torch not installed; using stub credibility")
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
        logger.info(
            "Credibility model loaded from %s (device=%s, labels=%s)",
            path,
            _device,
            _labels_map,
        )
        return True
    except Exception as e:
        logger.exception("Failed to load credibility model: %s", e)
        _model = None
        _tokenizer = None
        _device = None
        _labels_map = dict(_DEFAULT_ID2LABEL)
        _model_path_loaded = ""
        return False


def preload_credibility_model() -> dict[str, Any]:
    """
    Eager-load HF weights (or confirm stub mode). Intended for management commands / workers.
    """
    path = getattr(settings, "CREDIBILITY_MODEL_PATH", None) or ""
    if not path:
        return {"mode": "stub", "loaded": False, "reason": "CREDIBILITY_MODEL_PATH unset"}
    ok = _load_hf() if _model is None else True
    return {
        "mode": "hf" if ok and _model is not None else "stub",
        "loaded": bool(ok and _model is not None),
        "path": path or None,
        "device": str(_device) if _device is not None else None,
        "labels_map": dict(_labels_map),
    }


def _effective_threshold() -> float:
    path = getattr(settings, "CREDIBILITY_MODEL_PATH", None) or ""
    if path and os.path.isdir(path):
        meta_t = _read_metadata_threshold(path)
        if meta_t is not None:
            return meta_t
    return float(getattr(settings, "CREDIBILITY_CONFIDENCE_THRESHOLD", 0.6))


def predict_credibility(text: str) -> dict[str, Any]:
    """
    Returns dict: credibility_label (int), credibility_probs (list[float]),
    credibility_max_prob (float), credibility_model_id (str).
    Applies confidence threshold → may force label 2 (suspicious).
    """
    threshold = _effective_threshold()
    # MongoDB documents require string keys; keep labels map storage-safe.
    labels_out = _mongo_safe_labels_map(_labels_map)
    text = (text or "").strip()
    if not text:
        return {
            "credibility_label": 2,
            "credibility_probs": [0.0, 0.0, 1.0],
            "credibility_max_prob": 1.0,
            "credibility_model_id": "empty-text",
            "credibility_labels_map": labels_out,
        }

    if _model is None:
        _load_hf()

    if _model is not None and _tokenizer is not None:
        try:
            import torch

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
            label = pred
            if max_prob < threshold:
                label = 2
            mid = _model_path_loaded or getattr(settings, "CREDIBILITY_MODEL_PATH", "hf-local") or "hf-local"
            return {
                "credibility_label": label,
                "credibility_probs": probs,
                "credibility_max_prob": max_prob,
                "credibility_model_id": str(mid),
                "credibility_labels_map": labels_out,
            }
        except Exception as e:
            logger.exception("Inference error: %s", e)

    # Stub: short or exclamation-heavy → suspicious-leaning distribution
    stub_score = 0.55 if len(text) < 80 or text.count("!") > 3 else 0.72
    probs = [stub_score, (1 - stub_score) * 0.35, (1 - stub_score) * 0.65]
    s = sum(probs)
    probs = [p / s for p in probs]
    pred = int(max(range(3), key=lambda i: probs[i]))
    if max(probs) < threshold:
        pred = 2
    return {
        "credibility_label": pred,
        "credibility_probs": probs,
        "credibility_max_prob": max(probs),
        "credibility_model_id": "stub-heuristic",
        "credibility_labels_map": _mongo_safe_labels_map(_DEFAULT_ID2LABEL),
    }
