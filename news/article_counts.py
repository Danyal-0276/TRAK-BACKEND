"""Admin article count helpers — avoid double-counting raw + processed rows."""

from __future__ import annotations

from typing import Any

from news.moderation_rules import auto_approved_query


def count_unique_article_urls(raw_col, proc_col) -> int:
    """
    Distinct stories by canonical URL across raw_articles and processed_articles.
    After pipeline, the same article usually exists in both collections once.
    """
    try:
        proc_name = proc_col.name
        pipeline = [
            {
                "$project": {
                    "url": {
                        "$trim": {
                            "input": {"$toString": {"$ifNull": ["$canonical_url", ""]}},
                        }
                    }
                }
            },
            {"$match": {"url": {"$ne": ""}}},
            {
                "$unionWith": {
                    "coll": proc_name,
                    "pipeline": [
                        {
                            "$project": {
                                "url": {
                                    "$trim": {
                                        "input": {
                                            "$toString": {
                                                "$ifNull": [
                                                    "$canonical_url",
                                                    {"$ifNull": ["$raw_canonical_url", ""]},
                                                ]
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        {"$match": {"url": {"$ne": ""}}},
                    ],
                }
            },
            {"$group": {"_id": "$url"}},
            {"$count": "total"},
        ]
        rows = list(raw_col.aggregate(pipeline))
        if rows:
            return int(rows[0]["total"])
    except Exception:
        pass

    raw_n = raw_col.count_documents({})
    proc_n = proc_col.count_documents({})
    return max(raw_n, proc_n)


def build_admin_article_counts(
    *,
    raw_col,
    proc_col,
    filtered_total: int,
    review_query: dict[str, Any],
    use_unique_filtered: bool = False,
) -> dict[str, int]:
    raw_total = raw_col.count_documents({})
    proc_total = proc_col.count_documents({})
    total_unique = count_unique_article_urls(raw_col, proc_col)
    pipeline_backlog = raw_col.count_documents(
        {"pipeline_status": {"$in": ["pending", "processing", "failed"]}}
    )
    needs_review = proc_col.count_documents(review_query)
    moderation_approved = proc_col.count_documents({"moderation_status": "approved"})
    moderation_rejected = proc_col.count_documents({"moderation_status": "rejected"})
    auto_approved = proc_col.count_documents(auto_approved_query())

    display_filtered = proc_total if use_unique_filtered else filtered_total

    return {
        "total_unique": total_unique,
        "total_all": total_unique,
        "raw_total": raw_total,
        "processed_total": proc_total,
        "pipeline_backlog": pipeline_backlog,
        "filtered_total": display_filtered,
        "needs_review": needs_review,
        "moderation_approved": moderation_approved,
        "moderation_rejected": moderation_rejected,
        "auto_approved": auto_approved,
    }
