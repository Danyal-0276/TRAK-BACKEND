from __future__ import annotations

from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ReturnDocument
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from news.mongo_db import device_tokens_collection, notifications_collection, user_preferences_collection


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _serialize_notification(doc: dict) -> dict:
    created = doc.get("created_at")
    updated = doc.get("updated_at")
    return {
        "id": str(doc.get("_id")),
        "type": doc.get("type") or "system",
        "text": doc.get("text") or "",
        "details": doc.get("details") or "",
        "keyword": doc.get("keyword"),
        "read": bool(doc.get("read")),
        "important": bool(doc.get("important")),
        "meta": doc.get("meta") or {},
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
    }


class NotificationsListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        docs = (
            notifications_collection()
            .find({"user_id": request.user.pk})
            .sort("created_at", -1)
            .limit(200)
        )
        return Response({"results": [_serialize_notification(d) for d in docs]}, status=status.HTTP_200_OK)


class NotificationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, notification_id: str):
        try:
            oid = ObjectId(notification_id)
        except Exception:
            return Response({"detail": "Invalid notification id."}, status=status.HTTP_400_BAD_REQUEST)
        doc = notifications_collection().find_one({"_id": oid, "user_id": request.user.pk})
        if not doc:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_serialize_notification(doc), status=status.HTTP_200_OK)


class MarkNotificationReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, notification_id: str):
        try:
            oid = ObjectId(notification_id)
        except Exception:
            return Response({"detail": "Invalid notification id."}, status=status.HTTP_400_BAD_REQUEST)
        res = notifications_collection().find_one_and_update(
            {"_id": oid, "user_id": request.user.pk},
            {"$set": {"read": True, "updated_at": _utc_now()}},
            return_document=ReturnDocument.AFTER,
        )
        if not res:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(_serialize_notification(res), status=status.HTTP_200_OK)


class MarkAllNotificationsReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        now = _utc_now()
        notifications_collection().update_many(
            {"user_id": request.user.pk, "read": False},
            {"$set": {"read": True, "updated_at": now}},
        )
        return Response({"detail": "All notifications marked as read."}, status=status.HTTP_200_OK)


class NotificationPreferencesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        row = user_preferences_collection().find_one({"user_id": request.user.pk}) or {}
        return Response(
            {
                "push_enabled": bool(row.get("push_enabled", True)),
                "email_enabled": bool(row.get("email_enabled", True)),
                "keyword_alerts": bool(row.get("keyword_alerts", True)),
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request):
        allowed = {"push_enabled", "email_enabled", "keyword_alerts"}
        updates = {}
        for key in allowed:
            if key in request.data:
                try:
                    updates[key] = _parse_bool(request.data.get(key), key)
                except ValueError as exc:
                    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not updates:
            return Response({"detail": "No preference fields provided."}, status=status.HTTP_400_BAD_REQUEST)
        updates["updated_at"] = _utc_now()
        user_preferences_collection().update_one({"user_id": request.user.pk}, {"$set": updates}, upsert=True)
        row = user_preferences_collection().find_one({"user_id": request.user.pk}) or {}
        return Response(
            {
                "push_enabled": bool(row.get("push_enabled", True)),
                "email_enabled": bool(row.get("email_enabled", True)),
                "keyword_alerts": bool(row.get("keyword_alerts", True)),
            },
            status=status.HTTP_200_OK,
        )


class DeviceTokenRegisterView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        token = str(request.data.get("token") or "").strip()
        platform = str(request.data.get("platform") or "").strip().lower() or "unknown"
        if not token:
            return Response({"detail": "token is required."}, status=status.HTTP_400_BAD_REQUEST)
        device_tokens_collection().update_one(
            {"user_id": request.user.pk, "token": token},
            {"$set": {"platform": platform, "updated_at": _utc_now()}, "$setOnInsert": {"created_at": _utc_now()}},
            upsert=True,
        )
        return Response({"detail": "Token registered."}, status=status.HTTP_200_OK)

    def delete(self, request):
        token = str(request.data.get("token") or "").strip()
        if not token:
            return Response({"detail": "token is required."}, status=status.HTTP_400_BAD_REQUEST)
        device_tokens_collection().delete_one({"user_id": request.user.pk, "token": token})
        return Response({"detail": "Token removed."}, status=status.HTTP_200_OK)
