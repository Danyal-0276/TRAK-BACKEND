from datetime import datetime, timezone

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from news.services import article_query
from news.mongo_db import (
    bookmarks_collection,
    chatbot_history_collection,
    raw_collection,
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
            limit = min(int(request.query_params.get("limit", 50)), 100)
        except ValueError:
            limit = 50
        q = (request.query_params.get("q") or "").strip()
        data = article_query.get_user_feed(request.user, limit=limit, search_q=q)
        return Response({"results": data})


class ExploreFeedView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 200)), 500)
        except ValueError:
            limit = 200
        q = (request.query_params.get("q") or "").strip()
        data = article_query.get_explore_feed(limit=limit, search_q=q)
        return Response({"results": data})


class TrackKeywordsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        keywords = request.data.get("keywords")
        if keywords is None:
            return Response({"detail": "keywords required"}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(keywords, list):
            return Response({"detail": "keywords must be a list"}, status=status.HTTP_400_BAD_REQUEST)
        payload = article_query.upsert_user_keywords(request.user, keywords)
        return Response(payload, status=status.HTTP_200_OK)


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

        recent_raw = list(raw_collection().find().sort("fetched_at", -1).limit(3))
        if recent_raw:
            titles = [str(a.get("title") or "Untitled").strip() for a in recent_raw if a.get("title")]
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


class ReactionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        article_id = str(request.data.get("article_id") or "").strip()
        reaction = str(request.data.get("reaction") or "").strip().lower()
        if reaction not in {"like", "dislike", "none"}:
            return Response({"detail": "reaction must be like, dislike, or none."}, status=status.HTTP_400_BAD_REQUEST)
        if not article_id:
            return Response({"detail": "article_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        if reaction == "none":
            reactions_collection().delete_one({"user_id": request.user.pk, "article_id": article_id})
            return Response({"detail": "Reaction removed."}, status=status.HTTP_200_OK)
        reactions_collection().update_one(
            {"user_id": request.user.pk, "article_id": article_id},
            {"$set": {"reaction": reaction, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return Response({"detail": "Reaction saved.", "reaction": reaction}, status=status.HTTP_200_OK)
