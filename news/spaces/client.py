"""
Call Hugging Face Spaces (Gradio) for remote inference.

Configure with env vars such as SUMMARIZER_SPACE_ID / FAKE_DETECTION_SPACE_ID.
Optional HF_TOKEN for private Spaces.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_clients: dict[str, Any] = {}
_client_lock = threading.RLock()


def _hf_token() -> Optional[str]:
    token = getattr(settings, "HF_TOKEN", None) or ""
    return token.strip() or None


def _get_client(space_id: str):
    from gradio_client import Client

    space_id = (space_id or "").strip()
    if not space_id:
        raise ValueError("space_id is required")
    with _client_lock:
        if space_id not in _clients:
            kwargs: dict[str, Any] = {}
            token = _hf_token()
            if token:
                kwargs["hf_token"] = token
            _clients[space_id] = Client(space_id, **kwargs)
        return _clients[space_id]


def preload_space(space_id: str) -> dict[str, Any]:
    """Warm up Gradio client (validates Space is reachable)."""
    space_id = (space_id or "").strip()
    if not space_id:
        return {"loaded": False, "space_id": None, "reason": "space_id unset"}
    try:
        _get_client(space_id)
        return {"loaded": True, "space_id": space_id}
    except Exception as exc:
        logger.exception("Failed to connect to HF Space %s: %s", space_id, exc)
        return {"loaded": False, "space_id": space_id, "reason": str(exc)[:200]}


def space_predict(
    space_id: str,
    *args: Any,
    api_name: Optional[str] = None,
    **kwargs: Any,
) -> Any:
    """Run Space predict. Pass positional args matching the Gradio input order."""
    with _client_lock:
        client = _get_client(space_id.strip())
        predict_kwargs: dict[str, Any] = dict(kwargs)
        if api_name:
            predict_kwargs["api_name"] = api_name
        return client.predict(*args, **predict_kwargs)


def parse_summary_response(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    for key in ("summary", "text", "output", "result"):
                        if data.get(key):
                            return str(data[key]).strip()
            except json.JSONDecodeError:
                pass
        return text
    if isinstance(raw, dict):
        for key in ("summary", "text", "output", "result"):
            if raw.get(key):
                return str(raw[key]).strip()
        return str(raw).strip()
    if isinstance(raw, (list, tuple)) and raw:
        return parse_summary_response(raw[0])
    return str(raw).strip()


def _normalize_label_name(name: str) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _parse_percent_value(val: Any) -> float | None:
    """Parse '72%', 72, or 0.72 into a probability in [0, 1]."""
    if val is None:
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        v = float(val)
        return v / 100.0 if v > 1.0 else max(0.0, min(1.0, v))
    text = str(val).strip().replace(",", "")
    if not text:
        return None
    match = re.search(r"([\d.]+)", text)
    if not match:
        return None
    v = float(match.group(1))
    if "%" in text or v > 1.0:
        v /= 100.0
    return max(0.0, min(1.0, v))


def _try_parse_fake_news_detection_space(data: list | tuple) -> dict[str, Any] | None:
    """
    abd8433/fake-news-detection returns:
      (verdict, real_confidence_str, fake_confidence_str, news_info, debug)
    """
    if len(data) < 3:
        return None
    verdict_raw = str(data[0]).strip()
    norm = _normalize_label_name(verdict_raw)
    if norm not in {"real", "fake", "suspicious"}:
        return None
    real_p = _parse_percent_value(data[1])
    fake_p = _parse_percent_value(data[2])
    if real_p is None or fake_p is None:
        return None

    name_map = {"real": 0, "fake": 1, "suspicious": 2}
    label_id = name_map[norm]
    susp_p = max(0.0, 1.0 - real_p - fake_p)
    probs = [real_p, fake_p, susp_p]
    total = sum(probs)
    if total > 0:
        probs = [p / total for p in probs]
    else:
        probs = [1 / 3, 1 / 3, 1 / 3]

    return {
        "label_id": label_id,
        "label_name": verdict_raw,
        "probs": probs,
        "max_prob": float(probs[label_id]),
    }


def parse_classification_response(raw: Any) -> dict[str, Any]:
    """
    Normalize Space output to label_id, label_name, probs (optional), max_prob.
    Supports JSON dict, (label, prob), (label, [probs]), label string, etc.
    """
    label_name = ""
    label_id: Optional[int] = None
    probs: list[float] = []
    max_prob = 0.0

    data = raw
    if isinstance(raw, str):
        text = raw.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            label_name = text

    if isinstance(data, dict):
        label_name = str(
            data.get("label")
            or data.get("prediction")
            or data.get("class")
            or data.get("pred_label")
            or ""
        ).strip()
        if data.get("pred_label_id") is not None:
            try:
                label_id = int(data["pred_label_id"])
            except (TypeError, ValueError):
                label_id = None
        raw_probs = data.get("probs") or data.get("probabilities") or data.get("scores")
        if raw_probs is not None:
            probs = [float(p) for p in list(raw_probs)]
        conf = data.get("confidence") or data.get("max_prob") or data.get("score")
        if conf is not None:
            max_prob = float(conf)
    elif isinstance(data, (list, tuple)):
        space_parsed = _try_parse_fake_news_detection_space(data)
        if space_parsed:
            return space_parsed
        if len(data) >= 1:
            first = data[0]
            if isinstance(first, (int, float)) and not isinstance(first, bool):
                label_id = int(first)
            else:
                label_name = str(first).strip()
        if len(data) >= 2:
            second = data[1]
            if isinstance(second, (list, tuple)):
                probs = [float(p) for p in second]
            elif isinstance(second, (int, float)) and not isinstance(second, bool):
                max_prob = float(second)
            else:
                pct = _parse_percent_value(second)
                if pct is not None and len(data) >= 3:
                    fake_pct = _parse_percent_value(data[2])
                    if fake_pct is not None:
                        real_p, fake_p = pct, fake_pct
                        susp_p = max(0.0, 1.0 - real_p - fake_p)
                        probs = [real_p, fake_p, susp_p]
                        total = sum(probs)
                        if total > 0:
                            probs = [p / total for p in probs]
                elif not label_name:
                    label_name = str(second).strip()

    name_map = {
        "real": 0,
        "true": 0,
        "legitimate": 0,
        "fake": 1,
        "false": 1,
        "misinformation": 1,
        "suspicious": 2,
        "uncertain": 2,
        "mixed": 2,
    }
    if label_id is None and label_name:
        label_id = name_map.get(_normalize_label_name(label_name))

    if probs:
        max_prob = max(probs) if max_prob <= 0 else max_prob
        if label_id is None:
            label_id = int(max(range(len(probs)), key=lambda i: probs[i]))
    elif max_prob <= 0 and label_id is not None:
        max_prob = 0.55

    if label_id is None:
        label_id = 2

    return {
        "label_id": int(label_id),
        "label_name": label_name or str(label_id),
        "probs": probs,
        "max_prob": float(max_prob),
    }
