"""Semantic embeddings for custom keyword ↔ article matching."""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

_embedder = None
_embedder_model_id = ""
_embedder_lock = threading.Lock()


def _default_model_id() -> str:
    return (
        getattr(settings, "KEYWORD_EMBEDDING_MODEL", None)
        or "sentence-transformers/all-MiniLM-L6-v2"
    ).strip()


def _enabled() -> bool:
    raw = str(getattr(settings, "KEYWORD_EMBEDDING_ENABLED", "true")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _similarity_threshold() -> float:
    try:
        return float(getattr(settings, "KEYWORD_EMBEDDING_THRESHOLD", 0.42))
    except (TypeError, ValueError):
        return 0.42


def _max_input_chars() -> int:
    try:
        return max(128, int(getattr(settings, "KEYWORD_EMBEDDING_MAX_CHARS", 2000)))
    except (TypeError, ValueError):
        return 2000


def _get_embedder():
    global _embedder, _embedder_model_id
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is not None:
            return _embedder
        model_id = _default_model_id()
        try:
            from sentence_transformers import SentenceTransformer

            _embedder = SentenceTransformer(model_id, device="cpu")
            _embedder_model_id = model_id
            logger.info("Keyword embedding model loaded: %s", model_id)
        except Exception as exc:
            logger.warning("Keyword embedding model unavailable: %s", exc)
            _embedder = None
            _embedder_model_id = ""
    return _embedder


def embedding_model_id() -> str:
    if _embedder is not None:
        return _embedder_model_id
    _get_embedder()
    return _embedder_model_id


def preload_embedding_model() -> bool:
    return _get_embedder() is not None


def article_embedding_text(*, title: str, summary: str, clean_text: str = "") -> str:
    parts = [str(title or "").strip(), str(summary or "").strip()]
    body = str(clean_text or "").strip()
    if body:
        parts.append(body[:600])
    text = ". ".join(p for p in parts if p)
    return text[: _max_input_chars()]


def _to_list(vec: Any) -> list[float]:
    if vec is None:
        return []
    if hasattr(vec, "tolist"):
        vec = vec.tolist()
    return [float(x) for x in vec]


def embed_text(text: str) -> list[float]:
    if not _enabled():
        return []
    model = _get_embedder()
    if model is None:
        return []
    src = str(text or "").strip()
    if len(src) < 2:
        return []
    try:
        vec = model.encode(src, normalize_embeddings=True, show_progress_bar=False)
        return _to_list(vec)
    except Exception as exc:
        logger.warning("Embedding encode failed: %s", exc)
        return []


def embed_article(*, title: str, summary: str, clean_text: str = "") -> list[float]:
    return embed_text(article_embedding_text(title=title, summary=summary, clean_text=clean_text))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def keyword_similarity(keyword: str, article_embedding: list[float]) -> float:
    if not article_embedding:
        return 0.0
    kw_vec = embed_text(keyword)
    if not kw_vec:
        return 0.0
    return cosine_similarity(kw_vec, article_embedding)


def keyword_matches_embedding(keyword: str, article_embedding: list[float]) -> bool:
    if not _enabled() or not article_embedding:
        return False
    return keyword_similarity(keyword, article_embedding) >= _similarity_threshold()
