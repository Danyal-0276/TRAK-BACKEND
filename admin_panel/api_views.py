from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from bson import ObjectId
from django.core.management import call_command
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from pymongo import ReturnDocument
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminRole, IsSuperAdminRole
from news.mongo_json import mongo_json
from news.mongo_db import bookmarks_collection, get_db, notifications_collection, processed_collection, raw_collection, user_preferences_collection
from news.credibility.score import (
    compute_credibility_score_from_doc,
    effective_credibility_probs,
    prob_breakdown,
    verdict_confidence_percent,
)
from admin_panel.analytics_snapshot import build_admin_analytics_snapshot
from news.pipeline import orchestrator
from news import platform_taxonomy
from news import feedback_service
from news.article_media import article_image_url, hydrate_processed_image_urls
from news.article_counts import build_admin_article_counts
from news.moderation_rules import (
    auto_approved_query,
    initial_moderation_status,
    needs_review_query,
)
from notifications.delivery import create_notification

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
        "image_url": article_image_url(doc),
        "pipeline_status": doc.get("pipeline_status"),
        "moderation_status": doc.get("moderation_status") or "review",
        "fetched_at": fa.isoformat() if hasattr(fa, "isoformat") else fa,
    }


def _credibility_label_prob(doc: dict) -> float | None:
    """Probability for the final credibility_label (not argmax across classes)."""
    label = doc.get("credibility_label")
    probs = effective_credibility_probs(doc) or doc.get("credibility_probs")
    if label is not None and isinstance(probs, list):
        try:
            idx = int(label)
            if 0 <= idx < len(probs):
                return float(probs[idx])
        except (TypeError, ValueError):
            pass
    max_prob = doc.get("credibility_max_prob")
    return float(max_prob) if max_prob is not None else None


def _credibility_label_name(doc: dict) -> str | None:
    label = doc.get("credibility_label")
    if label is None:
        return None
    labels_map = doc.get("credibility_labels_map") or {0: "real", 1: "fake", 2: "suspicious"}
    if isinstance(labels_map, dict):
        return labels_map.get(label) or labels_map.get(str(label))
    return str(label)


def _serialize_processed(doc: dict) -> dict:
    _id = doc.get("_id")
    pa = doc.get("processed_at")
    summary = doc.get("summary") or ""
    eff_probs = effective_credibility_probs(doc)
    return {
        "id": str(_id) if _id is not None else None,
        "scope": "processed",
        "canonical_url": doc.get("canonical_url") or doc.get("raw_canonical_url"),
        "title": doc.get("title"),
        "description": doc.get("description") or summary or doc.get("excerpt") or doc.get("clean_text") or doc.get("body_text"),
        "content": doc.get("content") or doc.get("article_text") or doc.get("text") or doc.get("clean_text") or doc.get("normalized_text") or doc.get("body_text"),
        "source_key": doc.get("source_key"),
        "image_url": article_image_url(doc),
        "summary": summary[:500] if summary else "",
        "topic_keywords": list(doc.get("topic_keywords") or [])[:12],
        "credibility_label": doc.get("credibility_label"),
        "credibility_label_name": _credibility_label_name(doc),
        "credibility_probs": eff_probs or doc.get("credibility_probs"),
        "credibility_label_prob": _credibility_label_prob(doc),
        "credibility_score": compute_credibility_score_from_doc(doc),
        "credibility_confidence_pct": verdict_confidence_percent(doc),
        "credibility_prob_breakdown": prob_breakdown(eff_probs) if eff_probs else None,
        "credibility_max_prob": doc.get("credibility_max_prob"),
        "fake_detection_label": doc.get("fake_detection_label"),
        "fake_detection_max_prob": doc.get("fake_detection_max_prob"),
        "fact_check_verdict": doc.get("fact_check_verdict"),
        "fact_check_hits": doc.get("fact_check_hits"),
        "fact_check_provider": doc.get("fact_check_provider"),
        "moderation_status": doc.get("moderation_status") or initial_moderation_status(doc),
        "processed_at": pa.isoformat() if hasattr(pa, "isoformat") else pa,
    }


def _pipeline_snapshot() -> dict:
    raw_col = raw_collection()
    proc_col = processed_collection()
    return {
        "raw_total": raw_col.count_documents({}),
        "raw_pending": raw_col.count_documents({"pipeline_status": "pending"}),
        "raw_processing": raw_col.count_documents({"pipeline_status": "processing"}),
        "raw_failed": raw_col.count_documents({"pipeline_status": "failed"}),
        "raw_done": raw_col.count_documents({"pipeline_status": "done"}),
        "processed_total": proc_col.count_documents({}),
    }


def _run_command_capture(name: str, **kwargs) -> dict:
    out = StringIO()
    err = StringIO()
    call_command(name, stdout=out, stderr=err, **kwargs)
    stdout = out.getvalue().strip()
    stderr = err.getvalue().strip()
    return {
        "stdout": stdout[-4000:] if stdout else "",
        "stderr": stderr[-4000:] if stderr else "",
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

    @staticmethod
    def _pipeline_query(pipeline_status: str) -> dict | None:
        ps = str(pipeline_status or "").strip().lower()
        if not ps:
            return None
        if ps == "queue":
            return {"pipeline_status": {"$in": ["pending", "processing"]}}
        if ps in {"pending", "processing", "failed", "done"}:
            return {"pipeline_status": ps}
        return None

    @staticmethod
    def _moderation_pending_clause() -> dict:
        return {
            "$or": [
                {"moderation_status": "review"},
                {"moderation_status": {"$exists": False}},
                {"moderation_status": None},
                {"moderation_status": ""},
            ]
        }

    @staticmethod
    def _is_needs_review_filter(moderation_status: str) -> bool:
        return str(moderation_status or "").strip().lower() in {"review", "needs_review", "needs-review"}

    @staticmethod
    def _moderation_query(moderation_status: str) -> dict | None:
        ms = str(moderation_status or "").strip().lower()
        if not ms:
            return None
        if AdminArticlesView._is_needs_review_filter(ms):
            return needs_review_query()
        if ms in {"approved", "rejected"}:
            return {"moderation_status": ms}
        return None

    @staticmethod
    def _credibility_label_query(credibility_label: str) -> dict | None:
        raw = str(credibility_label or "").strip().lower()
        if not raw:
            return None
        label_map = {"fake": 1, "suspicious": 2, "real": 0, "0": 0, "1": 1, "2": 2}
        if raw in label_map:
            return {"credibility_label": label_map[raw]}
        try:
            idx = int(raw)
            if idx in (0, 1, 2):
                return {"credibility_label": idx}
        except ValueError:
            pass
        return None

    @staticmethod
    def _merge_queries(*parts: dict | None) -> dict:
        queries = [q for q in parts if q]
        if not queries:
            return {}
        if len(queries) == 1:
            return queries[0]
        return {"$and": queries}

    @staticmethod
    def _serialize_processed_list(docs, raw_col) -> list[dict]:
        doc_list = list(docs)
        hydrate_processed_image_urls(doc_list, raw_col)
        return [_serialize_processed(doc) for doc in doc_list]

    def get(self, request):
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(100, max(1, int(request.query_params.get("page_size", 20))))
        except ValueError:
            return Response({"detail": "Invalid pagination"}, status=status.HTTP_400_BAD_REQUEST)

        scope = (request.query_params.get("scope") or "all").lower()
        pipeline_status = (
            request.query_params.get("pipeline_status")
            or request.query_params.get("pipeline")
            or ""
        )
        moderation_status = (
            request.query_params.get("moderation_status")
            or request.query_params.get("moderation")
            or ""
        )
        credibility_label = request.query_params.get("credibility_label") or ""
        needs_review_filter = self._is_needs_review_filter(moderation_status)
        if needs_review_filter or self._credibility_label_query(credibility_label):
            scope = "processed"
        skip = (page - 1) * page_size

        raw_col = raw_collection()
        proc_col = processed_collection()
        results: list[dict] = []

        if scope == "raw":
            query = self._merge_queries(
                self._pipeline_query(pipeline_status),
                self._moderation_query(moderation_status),
                self._credibility_label_query(credibility_label),
            )
            ps_lower = str(pipeline_status or "").strip().lower()
            sort_dir = 1 if ps_lower in {"queue", "pending", "processing"} else -1
            cursor = raw_col.find(query).sort("fetched_at", sort_dir).skip(skip).limit(page_size)
            for doc in cursor:
                results.append(_serialize_raw(doc))
        elif scope == "processed":
            query = self._merge_queries(
                self._moderation_query(moderation_status),
                self._credibility_label_query(credibility_label),
            )
            proc_docs = list(
                proc_col.find(query).sort("processed_at", -1).skip(skip).limit(page_size)
            )
            results.extend(self._serialize_processed_list(proc_docs, raw_col))
        else:
            half = max(1, page_size // 2)
            raw_query = self._merge_queries(
                self._pipeline_query(pipeline_status),
                self._moderation_query(moderation_status),
                self._credibility_label_query(credibility_label),
            )
            proc_query = self._merge_queries(
                self._moderation_query(moderation_status),
                self._credibility_label_query(credibility_label),
            )
            for doc in raw_col.find(raw_query).sort("fetched_at", -1).limit(half):
                results.append(_serialize_raw(doc))
            proc_docs = list(
                proc_col.find(proc_query).sort("processed_at", -1).limit(page_size - half)
            )
            results.extend(self._serialize_processed_list(proc_docs, raw_col))

        review_query = needs_review_query()
        if scope == "raw":
            filtered_total = raw_col.count_documents(query)
        elif scope == "processed":
            filtered_total = proc_col.count_documents(query)
        else:
            filtered_total = raw_col.count_documents(raw_query) + proc_col.count_documents(proc_query)

        count_payload = build_admin_article_counts(
            raw_col=raw_col,
            proc_col=proc_col,
            filtered_total=filtered_total,
            review_query=review_query,
            use_unique_filtered=(
                scope == "processed"
                and not str(pipeline_status or "").strip()
                and not needs_review_filter
                and str(moderation_status or "").strip().lower() not in {"approved", "rejected"}
            ),
        )
        count_payload["returned"] = len(results)

        return Response({
            "page": page,
            "page_size": page_size,
            "scope": scope,
            "pipeline_status": str(pipeline_status or "").strip().lower() or None,
            "moderation_status": str(moderation_status or "").strip().lower() or None,
            "credibility_label": str(credibility_label or "").strip().lower() or None,
            "counts": count_payload,
            "results": results,
        })


class AdminArticleImageProxyView(APIView):
    """Stream external article images for admin web (hotlink / CSP fallback)."""

    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request):
        raw_url = str(request.query_params.get("url") or "").strip()
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return Response({"detail": "Invalid image URL."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            req = Request(raw_url, headers={"User-Agent": "TRAK-Admin/1.0", "Accept": "image/*,*/*"})
            with urlopen(req, timeout=12) as remote:
                content_type = remote.headers.get("Content-Type") or "image/jpeg"
                if not str(content_type).lower().startswith("image/"):
                    return Response({"detail": "URL is not an image."}, status=status.HTTP_400_BAD_REQUEST)
                body = remote.read(5_000_000)
            response = HttpResponse(body, content_type=content_type)
            response["Cache-Control"] = "private, max-age=3600"
            return response
        except Exception as exc:
            return Response({"detail": f"Could not fetch image: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)


class AdminAnalyticsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, _request):
        return Response(build_admin_analytics_snapshot())


class AdminArticleDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request, scope: str, article_id: str):
        try:
            col, oid = _resolve_article(scope, article_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        doc = col.find_one({"_id": oid})
        if not doc:
            return Response({"detail": "Article not found."}, status=status.HTTP_404_NOT_FOUND)
        if scope == "processed":
            hydrate_processed_image_urls([doc], raw_collection())
            return Response(_serialize_processed(doc))
        return Response(_serialize_raw(doc))

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
        if scope == "processed":
            hydrate_processed_image_urls([updated], raw_collection())
            if moderation_status == "approved":
                try:
                    from news.notifications.keyword_alerts import notify_keyword_matches_for_article

                    notify_keyword_matches_for_article(updated)
                except Exception:
                    pass
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


class AdminArticleLookupView(APIView):
    """Resolve article by id across processed then raw collections."""

    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request):
        article_id = str(request.query_params.get("id") or "").strip()
        if not article_id:
            return Response({"detail": "id query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)
        raw_col = raw_collection()
        for scope in ("processed", "raw"):
            try:
                col, oid = _resolve_article(scope, article_id)
            except ValueError:
                continue
            doc = col.find_one({"_id": oid})
            if not doc:
                continue
            if scope == "processed":
                hydrate_processed_image_urls([doc], raw_col)
                return Response(_serialize_processed(doc))
            return Response(_serialize_raw(doc))
        return Response({"detail": "Article not found."}, status=status.HTTP_404_NOT_FOUND)


def _parse_drain_queue_flag(value, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return default


class AdminPipelineRunView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def post(self, request):
        try:
            batch_size = min(500, max(1, int(request.data.get("limit", 10))))
        except (TypeError, ValueError):
            batch_size = 10
        drain_queue = _parse_drain_queue_flag(request.data.get("drain_queue"), default=True)
        workers = max(1, min(8, int(getattr(settings, "PIPELINE_WORKERS", 1))))
        try:
            orchestrator.heal_stuck_raw_pipeline(
                stale_minutes=getattr(settings, "PIPELINE_STALE_MINUTES", 30)
            )
        except Exception:
            pass
        before = _pipeline_snapshot()
        if drain_queue:
            result = orchestrator.run_until_empty(batch_size=batch_size, workers=workers)
        else:
            result = orchestrator.run_batch(limit=batch_size, workers=workers)
        after = _pipeline_snapshot()
        result = {
            **result,
            "drain_queue": drain_queue,
            "batch_size": batch_size,
            "before": before,
            "after": after,
        }
        try:
            from notifications.admin_alerts import notify_admin_pipeline_batch

            notify_admin_pipeline_batch(
                processed_ok=int(result.get("processed_ok") or 0),
                errors=int(result.get("errors") or 0),
            )
        except Exception:
            pass
        try:
            from news.notifications.keyword_alerts import notify_keyword_matches_for_recent_articles

            result["keyword_notifications_sent"] = notify_keyword_matches_for_recent_articles(
                hours=168, limit=200
            )
        except Exception:
            result["keyword_notifications_sent"] = 0
        return Response(result, status=status.HTTP_200_OK)


class AdminScrapeRunView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def post(self, request):
        try:
            scrape_limit = min(200, max(1, int(request.data.get("limit", 40))))
        except (TypeError, ValueError):
            scrape_limit = 40

        before = _pipeline_snapshot()
        logs = _run_command_capture(
            "run_news_cycle",
            skip_pipeline=True,
            scrape_limit=scrape_limit,
            total_limit=scrape_limit,
        )
        after = _pipeline_snapshot()
        try:
            from news.pipeline.auto_runner import schedule_immediate_drain

            schedule_immediate_drain()
        except Exception:
            pass
        return Response(
            {
                "mode": "scrape_only",
                "scrape_limit": scrape_limit,
                "total_limit": scrape_limit,
                "before": before,
                "after": after,
                "delta": {
                    "raw_total": after["raw_total"] - before["raw_total"],
                    "pending": after["raw_pending"] - before["raw_pending"],
                    "processed_total": after["processed_total"] - before["processed_total"],
                },
                "logs": logs,
            },
            status=status.HTTP_200_OK,
        )


class AdminScrapeAndPipelineRunView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def post(self, request):
        try:
            scrape_limit = min(200, max(1, int(request.data.get("scrape_limit", 40))))
        except (TypeError, ValueError):
            scrape_limit = 40
        try:
            pipeline_limit = min(1000, max(1, int(request.data.get("pipeline_limit", 200))))
        except (TypeError, ValueError):
            pipeline_limit = 200

        workers = max(1, min(8, int(getattr(settings, "PIPELINE_WORKERS", 1))))
        before = _pipeline_snapshot()
        logs = _run_command_capture(
            "run_news_cycle",
            skip_pipeline=True,
            scrape_limit=scrape_limit,
            total_limit=scrape_limit,
        )
        mid = _pipeline_snapshot()
        try:
            orchestrator.heal_stuck_raw_pipeline(
                stale_minutes=getattr(settings, "PIPELINE_STALE_MINUTES", 30)
            )
        except Exception:
            pass
        pipeline_result = orchestrator.run_until_empty(
            batch_size=min(pipeline_limit, 500),
            workers=workers,
        )
        after = _pipeline_snapshot()
        return Response(
            {
                "mode": "scrape_and_pipeline",
                "scrape_limit": scrape_limit,
                "total_limit": scrape_limit,
                "pipeline_limit": pipeline_limit,
                "before": before,
                "after": after,
                "mid": mid,
                "pipeline": pipeline_result,
                "processed_ok": pipeline_result.get("processed_ok", 0),
                "errors": pipeline_result.get("errors", 0),
                "delta": {
                    "raw_total": after["raw_total"] - before["raw_total"],
                    "pending": after["raw_pending"] - before["raw_pending"],
                    "processed_total": after["processed_total"] - before["processed_total"],
                },
                "logs": logs,
            },
            status=status.HTTP_200_OK,
        )


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


def _serialize_admin_user(u: User) -> dict:
    return {
        "id": str(u.pk),
        "email": u.email,
        "role": u.role,
        "is_active": bool(u.is_active),
        "is_super_admin": bool(getattr(u, "is_super_admin", False)),
        "email_verified": bool(getattr(u, "email_verified", False)),
        "created_at": u.created_at,
    }


def _admin_user_profile(user_id) -> dict:
    profile = get_db()["user_profiles"].find_one({"user_id": user_id}) or {}
    return {
        "full_name": profile.get("full_name") or "",
        "username": profile.get("username") or "",
        "phone": profile.get("phone") or "",
        "phone_verified": bool(profile.get("phone_verified")),
        "bio": profile.get("bio") or "",
        "avatar_image": profile.get("avatar_image") or "",
    }


def _admin_user_bookmarks(user_id, limit: int = 50) -> list[dict]:
    rows = list(
        bookmarks_collection()
        .find({"user_id": user_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    out: list[dict] = []
    proc = processed_collection()
    for row in rows:
        aid = row.get("article_id")
        title = row.get("title") or ""
        url = row.get("url") or row.get("canonical_url") or ""
        if aid and not title:
            try:
                doc = proc.find_one({"_id": ObjectId(str(aid))}, {"title": 1, "canonical_url": 1})
            except Exception:
                doc = None
            if doc:
                title = doc.get("title") or title
                url = doc.get("canonical_url") or url
        out.append(
            {
                "article_id": str(aid) if aid is not None else "",
                "title": title or "Untitled",
                "url": url,
                "created_at": row.get("created_at"),
            }
        )
    return out


class AdminUsersView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request):
        q = str(request.query_params.get("q") or "").strip().lower()
        role_filter = str(request.query_params.get("role") or "all").strip().lower()
        users = User.objects.all().order_by("-created_at")
        if role_filter in {User.Role.ADMIN, User.Role.USER}:
            users = users.filter(role=role_filter)
        if q:
            users = users.filter(email__icontains=q)
        return Response({"results": [_serialize_admin_user(u) for u in users[:300]]})


class AdminAdminsCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole, IsSuperAdminRole]

    def post(self, request):
        email = str(request.data.get("email") or "").strip().lower()
        password = str(request.data.get("password") or "")
        if not email or "@" not in email:
            return Response({"detail": "Valid email is required."}, status=status.HTTP_400_BAD_REQUEST)
        if len(password) < 6:
            return Response({"detail": "Password must be at least 6 characters."}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(email__iexact=email).exists():
            return Response({"detail": "A user with this email already exists."}, status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.create_user(
            email,
            password,
            role=User.Role.ADMIN,
            is_staff=True,
            is_active=True,
            is_super_admin=False,
        )
        return Response(_serialize_admin_user(user), status=status.HTTP_201_CREATED)


class AdminUserDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request, user_id: str):
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        payload = {
            **_serialize_admin_user(user),
            **_admin_user_profile(user.pk),
            "bookmarks": _admin_user_bookmarks(user.pk),
        }
        return Response(payload, status=status.HTTP_200_OK)

    def patch(self, request, user_id: str):
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        update_fields = []
        if "role" in request.data:
            new_role = str(request.data["role"])
            if new_role not in {User.Role.ADMIN, User.Role.USER}:
                return Response({"detail": "Invalid role."}, status=status.HTTP_400_BAD_REQUEST)
            if not getattr(request.user, "is_super_admin", False):
                return Response(
                    {"detail": "Only super admins can change user roles."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if getattr(user, "is_super_admin", False) and new_role == User.Role.USER:
                return Response(
                    {"detail": "Cannot demote a super admin account."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            user.role = new_role
            update_fields.append("role")
        if "is_active" in request.data:
            try:
                user.is_active = _parse_bool(request.data.get("is_active"), "is_active")
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            update_fields.append("is_active")
        if update_fields:
            user.save(update_fields=update_fields)
        return Response({"detail": "User updated."}, status=status.HTTP_200_OK)

    def delete(self, request, user_id: str):
        if str(request.user.pk) == str(user_id):
            return Response({"detail": "Cannot delete current admin user."}, status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.filter(pk=user_id).first()
        if not user:
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        if str(user.role) == str(User.Role.ADMIN):
            if not getattr(request.user, "is_super_admin", False):
                return Response(
                    {"detail": "Only super admins can delete administrator accounts."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if getattr(user, "is_super_admin", False):
                return Response(
                    {"detail": "Cannot delete a super admin account."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        user.delete()
        return Response({"detail": "User deleted."}, status=status.HTTP_200_OK)


class AdminSettingsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, _request):
        platform_taxonomy.seed_taxonomy_if_empty()
        platform_taxonomy.seed_connections_if_empty()
        platform_taxonomy.merge_catalog_connections()
        row = user_preferences_collection().find_one({"scope": "admin_settings"}) or {}
        categories = platform_taxonomy.list_categories()
        connections = platform_taxonomy.list_connections()
        return Response(
            {
                "notifications_enabled_default": bool(row.get("notifications_enabled_default", True)),
                "allow_external_connections": bool(row.get("allow_external_connections", True)),
                "moderation_mode": str(row.get("moderation_mode") or "review"),
                "categories": categories,
                "connections": connections,
                "tags_with_subcategories": platform_taxonomy.tags_with_subcategories_map(categories),
                "language": str(row.get("language") or "English"),
                "timezone": str(row.get("timezone") or "UTC"),
            }
        )

    def patch(self, request):
        allowed = {
            "notifications_enabled_default",
            "allow_external_connections",
            "moderation_mode",
            "language",
            "timezone",
        }
        updates = {k: request.data.get(k) for k in allowed if k in request.data}
        if "categories" in request.data:
            platform_taxonomy.replace_categories(request.data.get("categories") or [])
        if "connections" in request.data:
            platform_taxonomy.replace_connections(request.data.get("connections") or [])
        if updates:
            user_preferences_collection().update_one(
                {"scope": "admin_settings"},
                {"$set": updates},
                upsert=True,
            )
        if not updates and "categories" not in request.data and "connections" not in request.data:
            return Response({"detail": "No updatable fields provided."}, status=status.HTTP_400_BAD_REQUEST)
        return self.get(request)


class AdminNotificationsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request):
        uid = request.user.pk
        rows = list(
            notifications_collection()
            .find({"user_id": {"$in": [uid, str(uid)]}, "audience": "admin"})
            .sort("created_at", -1)
            .limit(300)
        )
        return Response(
            {
                "results": [
                    {
                        "id": str(r.get("_id")),
                        "user_id": mongo_json(r.get("user_id")),
                        "type": r.get("type"),
                        "text": r.get("text"),
                        "details": r.get("details") or "",
                        "important": bool(r.get("important")),
                        "read": bool(r.get("read")),
                        "meta": mongo_json(r.get("meta") or {}),
                        "created_at": mongo_json(r.get("created_at")),
                    }
                    for r in rows
                ]
            }
        )

    def post(self, request):
        user_id = str(request.data.get("user_id") or "").strip()
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
        create_notification(
            user_id,
            ntype=payload["type"],
            text=payload["text"],
            details=payload["details"],
            important=payload["important"],
            audience="user",
        )
        return Response({"detail": "Notification created."}, status=status.HTTP_201_CREATED)


class AdminNotificationMarkReadView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def post(self, request, notification_id: str):
        try:
            oid = ObjectId(notification_id)
        except Exception:
            return Response({"detail": "Invalid notification id."}, status=status.HTTP_400_BAD_REQUEST)
        uid = request.user.pk
        res = notifications_collection().find_one_and_update(
            {
                "_id": oid,
                "user_id": {"$in": [uid, str(uid)]},
                "audience": "admin",
            },
            {"$set": {"read": True, "updated_at": datetime.now(timezone.utc)}},
            return_document=ReturnDocument.AFTER,
        )
        if not res:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "id": str(res.get("_id")),
                "read": True,
                "type": res.get("type"),
                "text": res.get("text"),
            }
        )


class AdminFeedbackListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 50)), 200)
        except ValueError:
            limit = 50
        try:
            skip = max(int(request.query_params.get("skip", 0)), 0)
        except ValueError:
            skip = 0
        rows = feedback_service.list_feedback(
            status=str(request.query_params.get("status") or "").strip(),
            fb_type=str(request.query_params.get("type") or "").strip(),
            category=str(request.query_params.get("category") or "").strip(),
            article_id=str(request.query_params.get("article_id") or "").strip(),
            limit=limit,
            skip=skip,
        )
        return Response({"results": rows, "stats": feedback_service.get_feedback_stats()})


class AdminFeedbackStatsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, _request):
        return Response(feedback_service.get_feedback_stats())


class AdminFeedbackDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, _request, feedback_id: str):
        doc = feedback_service.get_feedback_by_id(feedback_id)
        if not doc:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(doc)

    def patch(self, request, feedback_id: str):
        status_val = str(request.data.get("status") or "").strip()
        admin_notes = request.data.get("admin_notes")
        set_notes = "admin_notes" in request.data
        doc = feedback_service.update_feedback(
            feedback_id,
            admin_user=request.user,
            status=status_val,
            admin_notes=str(admin_notes) if admin_notes is not None else None,
            set_admin_notes=set_notes,
        )
        if not doc:
            return Response({"detail": "Not found or invalid status."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(doc)


class AdminCategoriesView(APIView):
    """CRUD for onboarding/platform categories (MongoDB admin_settings.categories)."""

    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, _request):
        return Response({"categories": platform_taxonomy.list_categories()})

    def post(self, request):
        name = str(request.data.get("name") or "").strip()
        subs = request.data.get("subcategories") or []
        if not isinstance(subs, list):
            return Response({"detail": "subcategories must be a list"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            created = platform_taxonomy.create_category(name, subs)
            return Response(created, status=status.HTTP_201_CREATED)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class AdminCategoryDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def patch(self, request, category_slug):
        try:
            updated = platform_taxonomy.update_category(
                category_slug,
                name=request.data.get("name"),
                active=request.data.get("active") if "active" in request.data else None,
            )
            return Response(updated)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, _request, category_slug):
        try:
            platform_taxonomy.delete_category(category_slug)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)


class AdminCategorySubcategoriesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def post(self, request, category_slug):
        name = str(request.data.get("name") or "").strip()
        try:
            created = platform_taxonomy.add_subcategory(category_slug, name)
            return Response(created, status=status.HTTP_201_CREATED)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class AdminSubcategoryDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def patch(self, request, category_slug, sub_slug):
        name = request.data.get("name")
        if not name:
            return Response({"detail": "name is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            updated = platform_taxonomy.update_subcategory(category_slug, sub_slug, name=str(name))
            return Response(updated)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, _request, category_slug, sub_slug):
        try:
            platform_taxonomy.delete_subcategory(category_slug, sub_slug)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)


class AdminConnectionsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, _request):
        return Response({"connections": platform_taxonomy.list_connections()})

    def post(self, request):
        name = str(request.data.get("name") or "").strip()
        url = str(request.data.get("url") or "").strip()
        try:
            created = platform_taxonomy.create_connection(name, url)
            return Response(created, status=status.HTTP_201_CREATED)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class AdminConnectionDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def patch(self, request, connection_slug):
        try:
            updated = platform_taxonomy.update_connection(
                connection_slug,
                name=request.data.get("name"),
                url=request.data.get("url") if "url" in request.data else None,
                active=request.data.get("active") if "active" in request.data else None,
            )
            return Response(updated)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)

    def delete(self, _request, connection_slug):
        try:
            platform_taxonomy.delete_connection(connection_slug)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
