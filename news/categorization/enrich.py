"""Combine category classification + article embedding for the AI pipeline."""

from __future__ import annotations

from typing import Any

from news.categorization.embeddings import (
    embed_article,
    embedding_model_id,
)
from news.categorization.inference import predict_categories
from news.categorization.matching import infer_rule_categories_from_text


def enrich_article_ml_fields(
    *,
    title: str = "",
    summary: str = "",
    clean_text: str = "",
) -> dict[str, Any]:
    """
    Run zero-shot categories and store a semantic embedding for keyword matching.
    Safe to call when models are disabled/unavailable (returns empty fields).
    """
    cat = predict_categories(title=title, summary=summary, clean_text=clean_text)
    if not cat.get("primary_category"):
        rule_cat = infer_rule_categories_from_text(
            title=title,
            summary=summary,
            clean_text=clean_text,
        )
        if rule_cat.get("primary_category"):
            cat = {**cat, **rule_cat}
    embedding = embed_article(title=title, summary=summary, clean_text=clean_text)

    out: dict[str, Any] = {
        "primary_category": cat.get("primary_category") or "",
        "categories": list(cat.get("categories") or []),
        "category_scores": dict(cat.get("category_scores") or {}),
        "category_confidence": float(cat.get("category_confidence") or 0.0),
        "category_model_id": cat.get("category_model_id") or "",
        "match_embedding": embedding,
        "match_embedding_model_id": embedding_model_id() if embedding else "",
    }
    if not out["primary_category"]:
        out.pop("primary_category", None)
    if not out["categories"]:
        out.pop("categories", None)
    if not out["category_scores"]:
        out.pop("category_scores", None)
    if not out["match_embedding"]:
        out.pop("match_embedding", None)
        out.pop("match_embedding_model_id", None)
    return out
