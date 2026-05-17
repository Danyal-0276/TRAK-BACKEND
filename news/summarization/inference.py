"""
BART seq2seq summarization via Hugging Face (default: daniB2112/bart-large-cnn-news-summarizer).
Falls back to extractive (first sentences) if the model is unavailable.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_ID = "daniB2112/bart-large-cnn-news-summarizer"

_model = None
_tokenizer = None
_device = None
_model_id_loaded: str = ""


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


def extractive_summary(text: str, max_sentences: int = 2) -> str:
    """Fallback when HF summarizer is off or fails."""
    text = (text or "").strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:max_sentences]) if parts else text[:400]


def _model_source() -> str:
    return (
        getattr(settings, "SUMMARIZER_MODEL_ID", None)
        or _DEFAULT_MODEL_ID
    ).strip()


def _summarizer_enabled() -> bool:
    raw = str(getattr(settings, "SUMMARIZER_ENABLED", "true")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _load_hf() -> bool:
    global _model, _tokenizer, _device, _model_id_loaded
    model_id = _model_source()
    if not model_id:
        return False
    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        import torch
    except ImportError:
        logger.warning("transformers/torch not installed; using extractive summaries")
        return False
    try:
        _tokenizer = AutoTokenizer.from_pretrained(model_id)
        _model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
        _device = _pick_device()
        if _device is not None:
            _model = _model.to(_device)
        _model.eval()
        _model_id_loaded = model_id
        logger.info("Summarizer loaded from %s (device=%s)", model_id, _device)
        return True
    except Exception as exc:
        logger.exception("Failed to load summarizer model %s: %s", model_id, exc)
        _model = None
        _tokenizer = None
        _device = None
        _model_id_loaded = ""
        return False


def preload_summarizer_model() -> dict[str, Any]:
    if not _summarizer_enabled():
        return {"mode": "extractive", "loaded": False, "reason": "SUMMARIZER_ENABLED=false"}
    model_id = _model_source()
    if _model is None:
        ok = _load_hf()
    else:
        ok = True
    return {
        "mode": "bart" if ok and _model is not None else "extractive",
        "loaded": bool(ok and _model is not None),
        "model_id": model_id,
        "device": str(_device) if _device is not None else None,
    }


def summarize_text(text: str, *, title: str = "") -> dict[str, Any]:
    """
    Returns summary (str), summarizer_mode ('bart' | 'extractive'), summarizer_model_id (str).
    """
    cleaned = (text or "").strip()
    if not cleaned and title:
        cleaned = title.strip()
    if not cleaned:
        return {
            "summary": "",
            "summarizer_mode": "empty",
            "summarizer_model_id": "empty-text",
        }

    if not _summarizer_enabled():
        return {
            "summary": extractive_summary(cleaned),
            "summarizer_mode": "extractive",
            "summarizer_model_id": "disabled",
        }

    if _model is None:
        _load_hf()

    if _model is not None and _tokenizer is not None:
        try:
            import torch

            max_chars = int(getattr(settings, "SUMMARIZER_MAX_INPUT_CHARS", 4000))
            max_new = int(getattr(settings, "SUMMARIZER_MAX_NEW_TOKENS", 128))
            body = cleaned[:max_chars]

            inputs = _tokenizer(
                body,
                max_length=1024,
                truncation=True,
                return_tensors="pt",
            )
            if _device is not None:
                inputs = {k: v.to(_device) for k, v in inputs.items()}

            with torch.no_grad():
                output_ids = _model.generate(
                    **inputs,
                    max_new_tokens=max_new,
                    num_beams=4,
                    length_penalty=1.0,
                    early_stopping=True,
                )

            summary = _tokenizer.decode(output_ids[0], skip_special_tokens=True).strip()
            if not summary:
                summary = extractive_summary(cleaned)
                mode = "extractive"
            else:
                mode = "bart"
            return {
                "summary": summary[:10000],
                "summarizer_mode": mode,
                "summarizer_model_id": _model_id_loaded or _model_source(),
            }
        except Exception as exc:
            logger.exception("BART summarization failed: %s", exc)

    return {
        "summary": extractive_summary(cleaned),
        "summarizer_mode": "extractive",
        "summarizer_model_id": _model_id_loaded or "extractive-fallback",
    }
