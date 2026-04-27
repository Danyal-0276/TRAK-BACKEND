from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bson import ObjectId
from django.conf import settings
from django.contrib.auth import get_user_model
from pymongo import ReturnDocument
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminRole
from news.mongo_db import notifications_collection, processed_collection, raw_collection, user_preferences_collection
from news.pipeline import orchestrator
from notifications.realtime import fanout_notification

User = get_user_model()


def _parse_bool(value, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "1", "yes", "on"}:
            return True
        if v in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"{field_name} must be a boolean.")


def _serialize_raw(doc: dict) -> dict:
    _id = doc.get("_id")
    fa = doc.get("fetched_at")
    return {
        "id": str(_id) if _id is not None else None,
        "scope": "raw",
        "canonical_url": doc.get("canonical_url"),
        "title": doc.get("title"),
        "description": doc.get("description") or doc.get("summary") or doc.get("excerpt") or doc.get("clean_text") or doc.get("body_text"),
        "content": doc.get("content") or doc.get("article_text") or doc.get("text") or doc.get("clean_text") or doc.get("normalized_text") or doc.get("body_text"),
        "source_key": doc.get("source_key"),
        "pipeline_status": doc.get("pipeline_status"),
        "moderation_status": doc.get("moderation_status") or "review",
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
        "description": doc.get("description") or doc.get("summary") or doc.get("excerpt") or doc.get("clean_text") or doc.get("body_text"),
        "content": doc.get("content") or doc.get("article_text") or doc.get("text") or doc.get("clean_text") or doc.get("normalized_text") or doc.get("body_text"),
        "source_key": doc.get("source_key"),
        "credibility_label": doc.get("credibility_label"),
        "credibility_probs": doc.get("credibility_probs"),
        "moderation_status": doc.get("moderation_status") or "review",
        "processed_at": pa.isoformat() if hasattr(pa, "isoformat") else pa,
    }


def _resolve_article(scope: str, article_id: str):
    scope = str(scope or "").strip().lower()
    if scope not in {"raw", "processed"}:
        raise ValueError("scope must be raw or processed")
    try:
        oid = ObjectId(article_id)
    except Exception as exc:
        raise ValueError("Invalid article id.") from exc
    col = raw_collection() if scope == "raw" else processed_collection()
    return col, oid


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


class AdminArticleDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def patch(self, request, scope: str, article_id: str):
        try:
            col, oid = _resolve_article(scope, article_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        moderation_status = str(request.data.get("status") or "").strip().lower()
        allowed = {"review", "approved", "rejected"}
        if moderation_status not in allowed:
            return Response({"detail": "status must be review, approved, or rejected."}, status=status.HTTP_400_BAD_REQUEST)
        updated = col.find_one_and_update(
            {"_id": oid},
            {"$set": {"moderation_status": moderation_status, "updated_at": datetime.now(timezone.utc)}},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            return Response({"detail": "Article not found."}, status=status.HTTP_404_NOT_FOUND)
        payload = _serialize_raw(updated) if scope == "raw" else _serialize_processed(updated)
        return Response(payload, status=status.HTTP_200_OK)

    def delete(self, request, scope: str, article_id: str):
        try:
            col, oid = _resolve_article(scope, article_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        result = col.delete_one({"_id": oid})
        if result.deleted_count == 0:
            return Response({"detail": "Article not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"detail": "Article deleted."}, status=status.HTTP_200_OK)


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


class AdminUsersView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request):
        q = str(request.query_params.get("q") or "").strip().lower()
        users = User.objects.all().order_by("-created_at")
        if q:
            users = users.filter(email__icontains=q)
        return Response(
            {
                "results": [
                    {
                        "id": u.pk,
                        "email": u.email,
                        "role": u.role,
                        "is_active": bool(u.is_active),
                        "created_at": u.created_at,
                    }
                    for u in users[:300]
                ]
            }
        )


class AdminUserDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def patch(self, request, user_id: int):
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        if "role" in request.data and str(request.data["role"]) in {User.Role.ADMIN, User.Role.USER}:
            user.role = str(request.data["role"])
        if "is_active" in request.data:
            try:
                user.is_active = _parse_bool(request.data.get("is_active"), "is_active")
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        user.save(update_fields=["role", "is_active"])
        return Response({"detail": "User updated."}, status=status.HTTP_200_OK)

    def delete(self, request, user_id: int):
        if request.user.pk == user_id:
            return Response({"detail": "Cannot delete current admin user."}, status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        user.delete()
        return Response({"detail": "User deleted."}, status=status.HTTP_200_OK)


class AdminSettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, _request):
        row = user_preferences_collection().find_one({"scope": "admin_settings"}) or {}
        return Response(
            {
                "notifications_enabled_default": bool(row.get("notifications_enabled_default", True)),
                "allow_external_connections": bool(row.get("allow_external_connections", True)),
                "moderation_mode": str(row.get("moderation_mode") or "review"),
                "categories": row.get("categories") or [],
                "connections": row.get("connections") or [],
            }
        )

    def patch(self, request):
        allowed = {
            "notifications_enabled_default",
            "allow_external_connections",
            "moderation_mode",
            "categories",
            "connections",
        }
        updates = {k: request.data.get(k) for k in allowed if k in request.data}
        if not updates:
            return Response({"detail": "No updatable fields provided."}, status=status.HTTP_400_BAD_REQUEST)
        user_preferences_collection().update_one({"scope": "admin_settings"}, {"$set": updates}, upsert=True)
        return self.get(request)


class AdminNotificationsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request):
        rows = list(notifications_collection().find().sort("created_at", -1).limit(300))
        return Response(
            {
                "results": [
                    {
                        "id": str(r.get("_id")),
                        "user_id": r.get("user_id"),
                        "type": r.get("type"),
                        "text": r.get("text"),
                        "read": bool(r.get("read")),
                        "created_at": r.get("created_at"),
                    }
                    for r in rows
                ]
            }
        )

    def post(self, request):
        try:
            user_id = int(request.data.get("user_id") or 0)
        except (TypeError, ValueError):
            return Response({"detail": "user_id must be an integer."}, status=status.HTTP_400_BAD_REQUEST)
        if not user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            important = _parse_bool(request.data.get("important", False), "important")
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payload = {
            "user_id": user_id,
            "type": str(request.data.get("type") or "system"),
            "text": str(request.data.get("text") or "").strip(),
            "details": str(request.data.get("details") or "").strip(),
            "important": important,
            "read": False,
        }
        if not payload["text"]:
            return Response({"detail": "text is required."}, status=status.HTTP_400_BAD_REQUEST)
        payload["created_at"] = datetime.now(timezone.utc)
        payload["updated_at"] = payload["created_at"]
        inserted = notifications_collection().insert_one(payload)
        fanout_notification(
            user_id,
            {
                "id": str(inserted.inserted_id),
                "type": payload["type"],
                "text": payload["text"],
                "details": payload["details"],
                "important": payload["important"],
                "read": payload["read"],
                "created_at": payload["created_at"].isoformat(),
            },
        )
        return Response({"detail": "Notification created."}, status=status.HTTP_201_CREATED)
