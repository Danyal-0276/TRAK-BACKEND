"""Admin moderation rules for processed articles."""

from __future__ import annotations

from typing import Any

FACT_CHECK_SKIP = frozenset(
    {"", "skipped", "disabled", "no_api_key", "empty_query", "api_error", "no_hits"}
)


def fact_check_ran(doc: dict[str, Any] | None) -> bool:
    if not doc:
        return False
    verdict = str(doc.get("fact_check_verdict") or "").strip().lower()
    return bool(verdict) and verdict not in FACT_CHECK_SKIP


def _label_int(doc: dict[str, Any]) -> int | None:
    label = doc.get("credibility_label")
    if label is None:
        return None
    try:
        return int(label)
    except (TypeError, ValueError):
        return None


def is_real_label(doc: dict[str, Any] | None) -> bool:
    if not doc:
        return False
    code = _label_int(doc)
    if code == 0:
        return True
    name = str(doc.get("credibility_label_name") or "").lower()
    return name == "real"


def is_fake_or_suspicious_label(doc: dict[str, Any] | None) -> bool:
    if not doc:
        return False
    code = _label_int(doc)
    if code in {1, 2}:
        return True
    name = str(doc.get("credibility_label_name") or "").lower()
    return "fake" in name or "suspicious" in name


def initial_moderation_status(doc: dict[str, Any]) -> str:
    """Real → approved; Fake/Suspicious with fact-check → review; else approved."""
    if is_real_label(doc):
        return "approved"
    if is_fake_or_suspicious_label(doc) and fact_check_ran(doc):
        return "review"
    return "approved"


def moderation_pending_clause() -> dict[str, Any]:
    return {
        "$or": [
            {"moderation_status": "review"},
            {"moderation_status": {"$exists": False}},
            {"moderation_status": None},
            {"moderation_status": ""},
        ]
    }


def fake_or_suspicious_clause() -> dict[str, Any]:
    return {
        "$or": [
            {"credibility_label": {"$in": [1, 2]}},
            {"credibility_label_name": {"$regex": r"fake|suspicious", "$options": "i"}},
        ]
    }


def fact_check_ran_clause() -> dict[str, Any]:
    return {
        "fact_check_verdict": {
            "$exists": True,
            "$nin": list(FACT_CHECK_SKIP),
        }
    }


def needs_review_query() -> dict[str, Any]:
    """Processed Fake/Suspicious articles with fact-check, awaiting admin decision."""
    return {
        "$and": [
            moderation_pending_clause(),
            fake_or_suspicious_clause(),
            fact_check_ran_clause(),
        ]
    }


def auto_approved_query() -> dict[str, Any]:
    return {
        "$and": [
            {"moderation_status": "approved"},
            {
                "$or": [
                    {"credibility_label": 0},
                    {"credibility_label_name": {"$regex": r"^real", "$options": "i"}},
                ]
            },
        ]
    }


def doc_needs_review(doc: dict[str, Any] | None) -> bool:
    if not doc:
        return False
    ms = str(doc.get("moderation_status") or "").strip().lower()
    if ms in {"approved", "rejected"}:
        return False
    return is_fake_or_suspicious_label(doc) and fact_check_ran(doc)


def real_label_clause() -> dict[str, Any]:
    """Mongo filter: processed articles classified as Real (label 0)."""
    return {
        "$or": [
            {"credibility_label": 0},
            {"credibility_label_name": {"$regex": r"^real", "$options": "i"}},
        ]
    }


def user_feed_visibility_clause() -> dict[str, Any]:
    """Mongo pre-filter for user-facing feeds (explore, search, pics, personalized feed)."""
    return {
        "$and": [
            {"moderation_status": {"$nin": ["rejected", "review"]}},
            real_label_clause(),
        ]
    }


def article_visible_to_users(doc: dict[str, Any] | None) -> bool:
    """True when the article may appear in user feeds and keyword alerts (Real only)."""
    if not doc:
        return False
    ms = str(doc.get("moderation_status") or "").strip().lower()
    if ms in {"rejected", "review"}:
        return False
    if not is_real_label(doc):
        return False
    return True
