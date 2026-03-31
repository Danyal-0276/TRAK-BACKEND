from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminRole
from news.mongo_db import processed_collection, raw_collection
from news.pipeline import orchestrator


def _serialize_raw(doc: dict) -> dict:
    _id = doc.get("_id")
    fa = doc.get("fetched_at")
    return {
        "id": str(_id) if _id is not None else None,
        "scope": "raw",
        "canonical_url": doc.get("canonical_url"),
        "title": doc.get("title"),
        "source_key": doc.get("source_key"),
        "pipeline_status": doc.get("pipeline_status"),
        "fetched_at": fa.isoformat() if hasattr(fa, "isoformat") else fa,
    }


def _serialize_processed(doc: dict) -> dict:
    _id = doc.get("_id")
    pa = doc.get("processed_at")
    return {
        "id": str(_id) if _id is not None else None,
        "scope": "processed",
        "canonical_url": doc.get("canonical_url") or doc.get("raw_canonical_url"),
        "title": doc.get("title"),
        "credibility_label": doc.get("credibility_label"),
        "credibility_probs": doc.get("credibility_probs"),
        "processed_at": pa.isoformat() if hasattr(pa, "isoformat") else pa,
    }


class AdminArticlesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request):
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        except ValueError:
            return Response({"detail": "Invalid pagination"}, status=status.HTTP_400_BAD_REQUEST)

        scope = (request.query_params.get("scope") or "all").lower()
        skip = (page - 1) * page_size

        raw_col = raw_collection()
        proc_col = processed_collection()
        results: list[dict] = []

        if scope == "raw":
            for doc in raw_col.find().sort("fetched_at", -1).skip(skip).limit(page_size):
                results.append(_serialize_raw(doc))
        elif scope == "processed":
            for doc in proc_col.find().sort("processed_at", -1).skip(skip).limit(page_size):
                results.append(_serialize_processed(doc))
        else:
            half = max(1, page_size // 2)
            for doc in raw_col.find().sort("fetched_at", -1).limit(half):
                results.append(_serialize_raw(doc))
            for doc in proc_col.find().sort("processed_at", -1).limit(page_size - half):
                results.append(_serialize_processed(doc))

        return Response({"page": page, "page_size": page_size, "scope": scope, "results": results})


class AdminAnalyticsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, _request):
        raw_col = raw_collection()
        proc_col = processed_collection()

        pipeline_counts: dict[str, int] = {}
        for doc in raw_col.aggregate(
            [{"$group": {"_id": "$pipeline_status", "count": {"$sum": 1}}}]
        ):
            key = doc["_id"] or "unknown"
            pipeline_counts[str(key)] = doc["count"]

        cred_counts: dict[str, int] = {}
        for doc in proc_col.aggregate(
            [{"$group": {"_id": "$credibility_label", "count": {"$sum": 1}}}]
        ):
            key = doc["_id"]
            cred_counts[str(key) if key is not None else "none"] = doc["count"]

        return Response(
            {
                "raw_total": raw_col.estimated_document_count(),
                "processed_total": proc_col.estimated_document_count(),
                "raw_by_pipeline_status": pipeline_counts,
                "processed_by_credibility_label": cred_counts,
            }
        )


class AdminPipelineRunView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def post(self, request):
        try:
            limit = min(500, max(1, int(request.data.get("limit", 10))))
        except (TypeError, ValueError):
            limit = 10
        result = orchestrator.run_batch(limit=limit)
        return Response(result, status=status.HTTP_200_OK)


class AdminModelMetricsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, _request):
        base = Path(settings.BASE_DIR)
        metrics_path = base / "ml_artifacts" / "credibility" / "latest" / "metrics.json"
        if not metrics_path.exists():
            return Response(
                {
                    "detail": "metrics.json not found. Train the model first.",
                    "expected_path": str(metrics_path),
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            with metrics_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
            return Response(
                {"detail": "Failed to read metrics.json", "error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(data, status=status.HTTP_200_OK)
