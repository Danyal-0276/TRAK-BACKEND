"""Predefined user feedback / report categories."""

from __future__ import annotations

FEEDBACK_CATEGORIES = {
    "incorrect_fact": "News appears factually incorrect",
    "misleading": "Misleading or out of context",
    "fake_source": "Suspected fake / unreliable source",
    "duplicate": "Duplicate or reposted content",
    "offensive": "Offensive or harmful content",
    "credibility_disagree": "I disagree with credibility rating",
    "other": "Other",
}

FEEDBACK_TYPES = frozenset({"article_report", "article_feedback", "app_feedback"})

FEEDBACK_STATUSES = frozenset({"pending", "reviewed", "dismissed"})

# Legacy report reasons mapped to categories
LEGACY_REASON_MAP = {
    "user_report": "misleading",
    "flag": "misleading",
}
