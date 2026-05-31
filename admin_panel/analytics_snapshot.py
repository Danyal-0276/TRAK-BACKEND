"""Build admin dashboard analytics snapshot from MongoDB + platform config."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from django.conf import settings

from news import platform_taxonomy
from news.mongo_db import processed_collection, raw_collection
from news.pipeline import orchestrator
from news.scrape_sources import connection_display_name, ingest_sources_summary

_CRED_LABELS = {"0": "Real", "1": "Fake", "2": "Suspicious", "none": "Unset"}

# Admin UI: whole-collection counts vs queue backlog.
COLLECTION_LABELS = {
    "raw_total": "Scraped articles (all pipeline states)",
    "processed_total": "AI output rows (may include stale until re-run)",
    "queued": "Raw articles pending or processing — use this for backlog",
}


def _group_counts(col, field: str, *, limit: int = 15) -> dict[str, int]:
    out: dict[str, int] = {}
    pipeline: list[dict[str, Any]] = [
        {"$match": {field: {"$exists": True, "$ne": None, "$ne": ""}}},
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]
    if limit > 0:
        pipeline.append({"$limit": limit})
    for doc in col.aggregate(pipeline):
        key = doc["_id"]
        out[str(key) if key is not None else "unknown"] = int(doc["count"])
    return out


def _daily_series(col, date_field: str, days: int = 14) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    buckets: dict[str, int] = {}
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        buckets[d] = 0

    try:
        cursor = col.aggregate(
            [
                {"$match": {date_field: {"$gte": start, "$type": "date"}}},
                {
                    "$group": {
                        "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": f"${date_field}"}},
                        "count": {"$sum": 1},
                    }
                },
            ]
        )
        for doc in cursor:
            day = str(doc.get("_id") or "")
            if day in buckets:
                buckets[day] = int(doc.get("count") or 0)
    except Exception:
        pass

    return [{"date": d, "label": d[5:], "count": buckets[d]} for d in sorted(buckets.keys())]


def _stale_processing_query(*, stale_minutes: int | None = None) -> dict[str, Any]:
    mins = stale_minutes if stale_minutes is not None else getattr(settings, "PIPELINE_STALE_MINUTES", 30)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(mins)))
    return {
        "pipeline_status": "processing",
        "$or": [
            {"processing_started_at": {"$lt": cutoff}},
            {"processing_started_at": {"$exists": False}},
        ],
    }


def _pipeline_summary(pipeline_counts: dict[str, int], raw_total: int, *, raw_col) -> dict[str, Any]:
    pending = int(pipeline_counts.get("pending") or 0)
    processing = int(pipeline_counts.get("processing") or 0)
    done = int(pipeline_counts.get("done") or 0)
    failed = int(pipeline_counts.get("failed") or 0)
    unknown = int(pipeline_counts.get("unknown") or 0)
    stale_processing = 0
    try:
        stale_processing = int(raw_col.count_documents(_stale_processing_query()))
    except Exception:
        stale_processing = 0
    active_processing = max(0, processing - stale_processing)
    queued = pending + active_processing
    finished = done + failed
    completion_pct = round(100.0 * done / max(1, raw_total), 1) if raw_total else 0.0
    success_pct = round(100.0 * done / max(1, finished), 1) if finished else 0.0
    return {
        "pending": pending,
        "processing": processing,
        "done": done,
        "failed": failed,
        "unknown": unknown,
        "stale_processing": stale_processing,
        "active_processing": active_processing,
        "queued": queued,
        "completion_pct": completion_pct,
        "success_pct": success_pct,
    }


def _count_processed_stale(raw_col, proc_col) -> int:
    """Raw pending/failed that still have a processed_articles row (stale AI output)."""
    proc_name = proc_col.name
    try:
        cursor = raw_col.aggregate(
            [
                {"$match": {"pipeline_status": {"$in": ["pending", "failed"]}}},
                {"$project": {"canonical_url": 1}},
                {
                    "$lookup": {
                        "from": proc_name,
                        "localField": "canonical_url",
                        "foreignField": "canonical_url",
                        "as": "proc",
                    }
                },
                {"$match": {"proc.0": {"$exists": True}}},
                {"$count": "n"},
            ]
        )
        doc = next(cursor, None)
        return int(doc["n"]) if doc else 0
    except Exception:
        return 0


def _connections_summary() -> dict[str, Any]:
    platform_taxonomy.refresh_connection_labels_from_catalog()
    connections = platform_taxonomy.list_connections()
    active = [c for c in connections if c.get("active", True)]
    ingest = ingest_sources_summary()
    return {
        "total": len(connections),
        "active": len(active),
        "sources": [
            {
                "slug": c.get("slug") or c.get("id"),
                "name": connection_display_name(c),
                "kind": c.get("kind") or "unknown",
                "active": bool(c.get("active", True)),
                "source_key": c.get("source_key") or c.get("slug"),
                "url": (c.get("url") or "").strip(),
            }
            for c in connections
        ],
        "sources_truncated": False,
        "ingest": ingest,
    }


def build_admin_analytics_snapshot() -> dict[str, Any]:
    platform_taxonomy.seed_taxonomy_if_empty()
    platform_taxonomy.seed_connections_if_empty()
    platform_taxonomy.merge_catalog_connections()
    raw_col = raw_collection()
    proc_col = processed_collection()

    try:
        orchestrator.heal_stuck_raw_pipeline(
            stale_minutes=getattr(settings, "PIPELINE_STALE_MINUTES", 30)
        )
    except Exception:
        pass

    raw_total = raw_col.estimated_document_count()
    processed_total = proc_col.estimated_document_count()

    pipeline_counts = _group_counts(raw_col, "pipeline_status", limit=0)
    cred_counts = _group_counts(proc_col, "credibility_label", limit=0)
    # Normalize credibility keys to strings
    cred_by_label: dict[str, int] = {}
    for k, v in cred_counts.items():
        cred_by_label[str(k) if k is not None else "none"] = v

    fact_check_counts = _group_counts(proc_col, "fact_check_verdict", limit=12)
    moderation_raw = _group_counts(raw_col, "moderation_status", limit=10)
    moderation_proc = _group_counts(proc_col, "moderation_status", limit=10)

    raw_by_source = _group_counts(raw_col, "source_key", limit=12)
    processed_by_source = _group_counts(proc_col, "source_key", limit=12)

    ingest_daily = _daily_series(raw_col, "fetched_at", days=14)
    processed_daily = _daily_series(proc_col, "processed_at", days=14)

    # Merge ingest + processed for chart
    activity_daily = []
    proc_map = {d["date"]: d["count"] for d in processed_daily}
    for row in ingest_daily:
        activity_daily.append(
            {
                "date": row["date"],
                "label": row["label"],
                "scraped": row["count"],
                "processed": proc_map.get(row["date"], 0),
            }
        )

    recent_failures = []
    for doc in raw_col.find(
        {"pipeline_status": "failed"},
        {"title": 1, "pipeline_error": 1, "source_key": 1, "fetched_at": 1},
    ).sort("fetched_at", -1).limit(6):
        err = doc.get("pipeline_error") or "Unknown error"
        recent_failures.append(
            {
                "title": (doc.get("title") or "Untitled")[:120],
                "source_key": doc.get("source_key") or "—",
                "error": str(err)[:200],
            }
        )

    users_total = 0
    users_active = 0
    try:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        users_total = User.objects.count()
        users_active = User.objects.filter(is_active=True).count()
    except Exception:
        pass

    pipeline_summary = _pipeline_summary(pipeline_counts, raw_total, raw_col=raw_col)
    pipeline_summary["processed_stale"] = _count_processed_stale(raw_col, proc_col)
    pipeline_summary["needs_pipeline"] = pipeline_summary["pending"] + pipeline_summary["failed"]
    connections = _connections_summary()

    return {
        "raw_total": raw_total,
        "processed_total": processed_total,
        "collection_labels": COLLECTION_LABELS,
        "raw_by_pipeline_status": pipeline_counts,
        "processed_by_credibility_label": cred_by_label,
        "processed_by_credibility_label_named": {
            _CRED_LABELS.get(str(k), str(k)): v for k, v in cred_by_label.items()
        },
        "pipeline_summary": pipeline_summary,
        "raw_by_source_key": raw_by_source,
        "processed_by_source_key": processed_by_source,
        "fact_check_by_verdict": fact_check_counts,
        "moderation_by_status_raw": moderation_raw,
        "moderation_by_status_processed": moderation_proc,
        "ingest_daily": ingest_daily,
        "processed_daily": processed_daily,
        "activity_daily": activity_daily,
        "scrape_connections": connections,
        "recent_pipeline_failures": recent_failures,
        "users_total": users_total,
        "users_active": users_active,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
