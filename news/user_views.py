from datetime import datetime, timezone

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from news.services import article_query
from news.mongo_db import (
    article_reports_collection,
    bookmarks_collection,
    chatbot_history_collection,
    processed_collection,
    reactions_collection,
    user_preferences_collection,
)


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


class UserFeedView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 30)), 100)
        except ValueError:
            limit = 30
        q = (request.query_params.get("q") or "").strip()
        cursor = (request.query_params.get("cursor") or "").strip() or None
        page = article_query.get_user_feed_page(
            request.user, limit=limit, search_q=q, cursor=cursor
        )
        return Response(page)


class ExploreFeedView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 30)), 200)
        except ValueError:
            limit = 30
        q = (request.query_params.get("q") or "").strip()
        cursor = (request.query_params.get("cursor") or "").strip() or None
        page = article_query.get_explore_feed_page(limit=limit, search_q=q, cursor=cursor)
        return Response(page)


class UserBootstrapView(APIView):
    """Single round-trip for home: keywords, feed page, bookmarks, reactions."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 30)), 50)
        except ValueError:
            limit = 30
        user = request.user
        keywords = article_query.list_user_keywords(user)
        if keywords:
            feed_page = article_query.get_user_feed_page(
                user, limit=limit, search_q="", cursor=None
            )
        else:
            feed_page = article_query.get_explore_feed_page(
                limit=limit, search_q="", cursor=None
            )
        bookmark_rows = list(
            bookmarks_collection().find({"user_id": user.pk}).sort("created_at", -1)
        )
        reaction_rows = list(reactions_collection().find({"user_id": user.pk}))
        return Response(
            {
                "keywords": keywords,
                "feed": feed_page,
                "bookmarks": {
                    "results": [
                        {
                            "id": str(r.get("_id")),
                            "article_id": r.get("article_id"),
                            "title": r.get("title"),
                            "url": r.get("url"),
                            "created_at": r.get("created_at"),
                        }
                        for r in bookmark_rows
                    ]
                },
                "reactions": {
                    "results": [
                        {
                            "article_id": str(r.get("article_id") or ""),
                            "reaction": str(r.get("reaction") or "none"),
                            "updated_at": r.get("updated_at"),
                        }
                        for r in reaction_rows
                        if r.get("article_id")
                    ]
                },
            }
        )


class PlatformCategoriesView(APIView):
    """Read-only platform categories/connections from admin settings."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, _request):
        row = user_preferences_collection().find_one({"scope": "admin_settings"}) or {}
        return Response(
            {
                "categories": row.get("categories") or [],
                "connections": row.get("connections") or [],
            }
        )


class UserKeywordsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        keywords = article_query.list_user_keywords(request.user)
        return Response({"keywords": keywords})


class TrackKeywordsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        keywords = request.data.get("keywords")
        if keywords is None:
            return Response({"detail": "keywords required"}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(keywords, list):
            return Response({"detail": "keywords must be a list"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = article_query.upsert_user_keywords(request.user, keywords)
            return Response(payload, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response(
                {"detail": f"Could not save keywords: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ArticleReportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        article_id = str(request.data.get("article_id") or "").strip()
        url = str(request.data.get("url") or "").strip()
        reason = str(request.data.get("reason") or "flag").strip() or "flag"
        col = article_reports_collection()
        doc = {
            "user_id": request.user.pk,
            "article_id": article_id or None,
            "url": url or None,
            "reason": reason[:2000],
            "created_at": datetime.now(timezone.utc),
        }
        col.insert_one(doc)
        return Response({"detail": "Report submitted."}, status=status.HTTP_201_CREATED)


class ArticleDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, article_id):
        doc = article_query.get_article_by_id(article_id, request.user)
        if doc is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(doc)


class ChatbotView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        message = str(request.data.get("message") or "").strip()
        if not message:
            return Response({"detail": "message is required"}, status=status.HTTP_400_BAD_REQUEST)

        feed = article_query.get_user_feed(request.user, limit=5, search_q=message)
        if feed:
            top = feed[0]
            extra_titles = [a.get("title") for a in feed[1:3] if a.get("title")]
            suggestions = ""
            if extra_titles:
                suggestions = "\n\nYou can also check:\n- " + "\n- ".join(extra_titles)
            payload = {
                "reply": (
                    f"Best match: {top.get('title')}.\n"
                    f"Source: {top.get('source') or 'unknown'}.\n"
                    "Open the article card for full details."
                    f"{suggestions}"
                ),
                "articles": feed,
            }
            _append_chatbot_history(request.user.pk, message, payload["reply"], payload.get("articles") or [])
            return Response(payload)

        recent_processed = list(
            processed_collection()
            .find({}, {"title": 1})
            .sort("processed_at", -1)
            .limit(3)
        )
        if recent_processed:
            titles = [
                str(a.get("title") or "Untitled").strip()
                for a in recent_processed
                if a.get("title")
            ]
            payload = {
                "reply": "I could not find an exact match, but here are recent headlines.",
                "headlines": titles,
            }
            _append_chatbot_history(request.user.pk, message, payload["reply"], [])
            return Response(payload)

        payload = {
            "reply": "No news data found yet. Run the scraper and then refresh the feed.",
            "articles": [],
        }
        _append_chatbot_history(request.user.pk, message, payload["reply"], [])
        return Response(payload)


def _append_chatbot_history(user_id: int, user_text: str, bot_text: str, articles: list[dict]) -> None:
    col = chatbot_history_collection()
    row = col.find_one({"user_id": user_id}) or {"user_id": user_id, "messages": []}
    messages = row.get("messages") or []
    messages.append({"role": "user", "text": user_text})
    top = articles[0] if articles else {}
    messages.append(
        {
            "role": "bot",
            "text": bot_text,
            "article_title": top.get("title"),
            "article_url": top.get("canonical_url"),
            "source": top.get("source"),
        }
    )
    # Keep only latest 50 chat messages (25 exchanges)
    messages = messages[-50:]
    col.update_one({"user_id": user_id}, {"$set": {"messages": messages}}, upsert=True)


class ChatbotHistoryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        col = chatbot_history_collection()
        row = col.find_one({"user_id": request.user.pk}) or {}
        return Response({"messages": row.get("messages") or []})

    def delete(self, request):
        col = chatbot_history_collection()
        col.update_one({"user_id": request.user.pk}, {"$set": {"messages": []}}, upsert=True)
        return Response({"detail": "Chat history cleared."}, status=status.HTTP_200_OK)


class UserPreferencesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        row = user_preferences_collection().find_one({"user_id": request.user.pk}) or {}
        return Response(
            {
                "notifications_enabled": bool(row.get("notifications_enabled", True)),
                "dark_mode_enabled": bool(row.get("dark_mode_enabled", False)),
                "personalization_enabled": bool(row.get("personalization_enabled", True)),
            }
        )

    def patch(self, request):
        allowed = {"notifications_enabled", "dark_mode_enabled", "personalization_enabled"}
        updates = {}
        for key in allowed:
            if key in request.data:
                try:
                    updates[key] = _parse_bool(request.data.get(key), key)
                except ValueError as exc:
                    return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if not updates:
            return Response({"detail": "No updatable fields provided."}, status=status.HTTP_400_BAD_REQUEST)
        user_preferences_collection().update_one({"user_id": request.user.pk}, {"$set": updates}, upsert=True)
        row = user_preferences_collection().find_one({"user_id": request.user.pk}) or {}
        return Response(
            {
                "notifications_enabled": bool(row.get("notifications_enabled", True)),
                "dark_mode_enabled": bool(row.get("dark_mode_enabled", False)),
                "personalization_enabled": bool(row.get("personalization_enabled", True)),
            }
        )


class BookmarkListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        rows = list(bookmarks_collection().find({"user_id": request.user.pk}).sort("created_at", -1))
        return Response(
            {
                "results": [
                    {
                        "id": str(r.get("_id")),
                        "article_id": r.get("article_id"),
                        "title": r.get("title"),
                        "url": r.get("url"),
                        "created_at": r.get("created_at"),
                    }
                    for r in rows
                ]
            }
        )

    def post(self, request):
        article_id = str(request.data.get("article_id") or "").strip()
        if not article_id:
            return Response({"detail": "article_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        payload = {
            "user_id": request.user.pk,
            "article_id": article_id,
            "title": str(request.data.get("title") or "").strip(),
            "url": str(request.data.get("url") or "").strip(),
        }
        bookmarks_collection().update_one(
            {"user_id": request.user.pk, "article_id": article_id},
            {"$set": payload, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return Response({"detail": "Bookmarked."}, status=status.HTTP_201_CREATED)


class BookmarkDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, article_id: str):
        bookmarks_collection().delete_one({"user_id": request.user.pk, "article_id": article_id})
        return Response({"detail": "Bookmark removed."}, status=status.HTTP_200_OK)


def _reaction_totals_for_article(article_id: str) -> tuple[int, int]:
    coll = reactions_collection()
    likes = coll.count_documents({"article_id": article_id, "reaction": "like"})
    dislikes = coll.count_documents({"article_id": article_id, "reaction": "dislike"})
    return likes, dislikes


class ReactionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        rows = list(reactions_collection().find({"user_id": request.user.pk}))
        return Response(
            {
                "results": [
                    {
                        "article_id": str(r.get("article_id") or ""),
                        "reaction": str(r.get("reaction") or "none"),
                        "updated_at": r.get("updated_at"),
                    }
                    for r in rows
                    if r.get("article_id")
                ]
            }
        )

    def post(self, request):
        article_id = str(request.data.get("article_id") or "").strip()
        reaction = str(request.data.get("reaction") or "").strip().lower()
        if reaction not in {"like", "dislike", "none"}:
            return Response({"detail": "reaction must be like, dislike, or none."}, status=status.HTTP_400_BAD_REQUEST)
        if not article_id:
            return Response({"detail": "article_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        if reaction == "none":
            reactions_collection().delete_one({"user_id": request.user.pk, "article_id": article_id})
            likes, dislikes = _reaction_totals_for_article(article_id)
            return Response(
                {
                    "detail": "Reaction removed.",
                    "reaction": "none",
                    "like_count": likes,
                    "dislike_count": dislikes,
                },
                status=status.HTTP_200_OK,
            )
        reactions_collection().update_one(
            {"user_id": request.user.pk, "article_id": article_id},
            {"$set": {"reaction": reaction, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        likes, dislikes = _reaction_totals_for_article(article_id)
        return Response(
            {
                "detail": "Reaction saved.",
                "reaction": reaction,
                "like_count": likes,
                "dislike_count": dislikes,
            },
            status=status.HTTP_200_OK,
        )
