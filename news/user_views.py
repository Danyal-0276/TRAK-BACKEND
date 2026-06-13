import threading
from datetime import datetime, timezone

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from news.services import article_query
from news.tts_service import (
    plan_article_tts_segments,
    synthesize_article_tts,
    synthesize_article_tts_segment,
    synthesize_article_tts_segments_batch,
)
from news.mongo_db import (
    article_reports_collection,
    bookmarks_collection,
    chatbot_history_collection,
    processed_collection,
    reactions_collection,
    user_preferences_collection,
)
from news import feedback_service
from news.chatbot import (
    ChatbotAPIError,
    ChatbotConfigError,
    fallback_reply,
    gather_news_context,
    generate_chatbot_reply,
    finalize_reply_with_article_cards,
    generate_greeting_reply,
    generate_identity_reply,
    generate_no_match_reply,
    generate_off_topic_reply,
    get_greeting_reply,
    get_identity_reply,
    get_no_match_reply,
    get_off_topic_reply,
    has_strong_article_match,
    is_chatbot_configured,
    pick_primary_article,
    sanitize_bot_reply,
    serialize_chat_article,
)
from news.chatbot.intents import (
    detect_intent,
    has_news_intent,
    is_off_topic_message,
    resolve_search_message,
)
from news.feedback_constants import FEEDBACK_CATEGORIES


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
        category = (request.query_params.get("category") or "").strip()
        cursor = (request.query_params.get("cursor") or "").strip() or None
        page = article_query.get_explore_feed_page(
            limit=limit, search_q=q, category=category, cursor=cursor
        )
        return Response(page)


class PicsFeedView(APIView):
    """Image-first article feed for the Pics browse experience."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 30)), 200)
        except ValueError:
            limit = 30
        q = (request.query_params.get("q") or "").strip()
        cursor = (request.query_params.get("cursor") or "").strip() or None
        page = article_query.get_pics_feed_page(limit=limit, search_q=q, cursor=cursor)
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
    """Read-only platform categories/connections for onboarding and browse screens."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, _request):
        from news.platform_taxonomy import get_public_taxonomy

        payload = get_public_taxonomy()
        raw_counts = article_query.get_primary_category_counts()
        payload["category_counts"] = {
            str(cat.get("slug") or "").strip(): int(raw_counts.get(str(cat.get("slug") or "").strip(), 0))
            for cat in (payload.get("categories") or [])
            if str(cat.get("slug") or "").strip()
        }
        return Response(payload)


class UserKeywordsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        keywords = article_query.list_user_keywords(request.user)
        return Response({"keywords": keywords})


def _schedule_keyword_alert_backfill(user) -> None:
    """Run keyword notification backfill off the request thread so saves return quickly."""
    user_id = getattr(user, "pk", None)
    if user_id is None:
        return

    def _run() -> None:
        try:
            from django.contrib.auth import get_user_model
            from news.notifications.keyword_alerts import notify_keyword_matches_for_user_recent

            u = get_user_model().objects.filter(pk=user_id).first()
            if u:
                notify_keyword_matches_for_user_recent(u, hours=48, limit=40)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


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
            _schedule_keyword_alert_backfill(request.user)
            return Response(payload, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response(
                {"detail": f"Could not save keywords: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UserFeedbackView(APIView):
    """POST user feedback / report; GET categories list."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "categories": [
                    {"key": k, "label": v} for k, v in FEEDBACK_CATEGORIES.items()
                ]
            }
        )

    def post(self, request):
        data, err, code = feedback_service.submit_user_feedback(
            request.user,
            fb_type=str(request.data.get("type") or ""),
            article_id=str(request.data.get("article_id") or ""),
            url=str(request.data.get("url") or ""),
            category=str(request.data.get("category") or ""),
            message=str(request.data.get("message") or ""),
            reason=str(request.data.get("reason") or ""),
        )
        if err:
            return Response({"detail": err}, status=code)
        return Response({"detail": "Feedback submitted.", "feedback": data}, status=code)


class ArticleReportView(APIView):
    """Legacy alias for POST /api/user/reports/."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data, err, code = feedback_service.submit_user_feedback(
            request.user,
            fb_type="article_report",
            article_id=str(request.data.get("article_id") or ""),
            url=str(request.data.get("url") or ""),
            category=str(request.data.get("category") or ""),
            message=str(request.data.get("message") or ""),
            reason=str(request.data.get("reason") or "flag"),
        )
        if err:
            return Response({"detail": err}, status=code)
        return Response({"detail": "Report submitted.", "feedback": data}, status=code)


class ArticleDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, article_id):
        doc = article_query.get_article_by_id(article_id, request.user)
        if doc is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(doc)


class ArticleTtsView(APIView):
    """Text-to-speech — merged audio (legacy). Prefer plan + chunk for streaming."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        text = str(request.data.get("text") or "").strip()
        language = str(request.data.get("language") or "english").lower().strip()
        if language not in ("english", "urdu"):
            return Response(
                {"detail": "language must be 'english' or 'urdu'"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not text:
            return Response({"detail": "text is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = synthesize_article_tts(text, language=language)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return Response({"detail": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(payload)


class ArticleTtsPlanView(APIView):
    """Return paragraph segments for progressive TTS (no synthesis)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        text = str(request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "text is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            segments = plan_article_tts_segments(text)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "segments": segments,
                "total": len(segments),
                "first_chunk_chars": segments[0][:120] if segments else "",
            }
        )


class ArticleTtsChunkView(APIView):
    """Synthesize a single segment — used while the next segment is prefetched."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        text = str(request.data.get("text") or "").strip()
        language = str(request.data.get("language") or "english").lower().strip()
        if language not in ("english", "urdu"):
            return Response(
                {"detail": "language must be 'english' or 'urdu'"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not text:
            return Response({"detail": "text is required"}, status=status.HTTP_400_BAD_REQUEST)
        tts_session_id = str(request.data.get("tts_session_id") or "").strip() or None
        voice = str(request.data.get("voice") or "").strip() or None
        try:
            payload = synthesize_article_tts_segment(
                text,
                language=language,
                tts_session_id=tts_session_id,
                voice=voice,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return Response({"detail": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(payload)


class ArticleTtsChunksView(APIView):
    """Synthesize up to 4 segments in parallel (faster prefetch)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        language = str(request.data.get("language") or "english").lower().strip()
        if language not in ("english", "urdu"):
            return Response(
                {"detail": "language must be 'english' or 'urdu'"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        raw = request.data.get("segments")
        if not isinstance(raw, list) or not raw:
            return Response({"detail": "segments must be a non-empty list"}, status=status.HTTP_400_BAD_REQUEST)
        segments = [str(s or "").strip() for s in raw if str(s or "").strip()]
        if not segments:
            return Response({"detail": "segments must contain text"}, status=status.HTTP_400_BAD_REQUEST)
        tts_session_id = str(request.data.get("tts_session_id") or "").strip() or None
        voice = str(request.data.get("voice") or "").strip() or None
        try:
            chunks = synthesize_article_tts_segments_batch(
                segments,
                language=language,
                tts_session_id=tts_session_id,
                voice=voice,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as e:
            return Response({"detail": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"chunks": chunks, "count": len(chunks)})


class ChatbotView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        message = str(request.data.get("message") or "").strip()
        if not message:
            return Response({"detail": "message is required"}, status=status.HTTP_400_BAD_REQUEST)

        history_row = chatbot_history_collection().find_one({"user_id": request.user.pk}) or {}
        prior_messages = history_row.get("messages") or []

        if detect_intent(message) == "greeting":
            try:
                reply = (
                    generate_greeting_reply(message, prior_messages)
                    if is_chatbot_configured()
                    else get_greeting_reply(message)
                )
            except ChatbotAPIError:
                reply = get_greeting_reply(message)
            payload = {
                "reply": sanitize_bot_reply(reply),
                "articles": [],
                "primary_article": None,
                "has_trak_article": False,
                "intent": "greeting",
                "powered_by": "gemini" if is_chatbot_configured() else "local",
            }
            _append_chatbot_history(request.user.pk, message, payload["reply"], None)
            return Response(payload)

        if is_off_topic_message(message, history=prior_messages):
            try:
                reply = (
                    generate_off_topic_reply(message, prior_messages)
                    if is_chatbot_configured()
                    else get_off_topic_reply()
                )
            except ChatbotAPIError:
                reply = get_off_topic_reply()
            payload = {
                "reply": sanitize_bot_reply(reply),
                "articles": [],
                "primary_article": None,
                "has_trak_article": False,
                "intent": "off_topic",
                "powered_by": "gemini" if is_chatbot_configured() else "local",
            }
            _append_chatbot_history(request.user.pk, message, payload["reply"], None)
            return Response(payload)

        intent = detect_intent(message, history=prior_messages)
        ctx_limit = 8 if intent != "summarize" else 6
        articles, intent = gather_news_context(
            request.user,
            message,
            limit=ctx_limit,
            history=prior_messages,
        )
        search_message = resolve_search_message(message, prior_messages)
        primary = pick_primary_article(search_message, articles)
        if intent == "headlines" and articles and not primary:
            primary = articles[0]
        db_match = has_strong_article_match(search_message, primary)

        if intent == "identity":
            try:
                reply = (
                    generate_identity_reply(message, prior_messages)
                    if is_chatbot_configured()
                    else get_identity_reply()
                )
            except ChatbotAPIError:
                reply = get_identity_reply()
            payload = {
                "reply": sanitize_bot_reply(reply),
                "articles": [],
                "primary_article": None,
                "has_trak_article": False,
                "intent": intent,
                "powered_by": "gemini" if is_chatbot_configured() else "local",
            }
            _append_chatbot_history(request.user.pk, message, payload["reply"], None)
            return Response(payload)

        if intent == "off_topic":
            try:
                reply = (
                    generate_off_topic_reply(message, prior_messages)
                    if is_chatbot_configured()
                    else get_off_topic_reply()
                )
            except ChatbotAPIError:
                reply = get_off_topic_reply()
            payload = {
                "reply": sanitize_bot_reply(reply),
                "articles": [],
                "primary_article": None,
                "has_trak_article": False,
                "intent": intent,
                "powered_by": "gemini" if is_chatbot_configured() else "local",
            }
            _append_chatbot_history(request.user.pk, message, payload["reply"], None)
            return Response(payload)

        if intent in ("no_match", "off_topic") or not articles:
            is_off = intent == "off_topic" or not has_news_intent(message, history=prior_messages)
            try:
                if is_off:
                    reply = (
                        generate_off_topic_reply(message, prior_messages)
                        if is_chatbot_configured()
                        else get_off_topic_reply()
                    )
                else:
                    reply = (
                        generate_no_match_reply(message, prior_messages)
                        if is_chatbot_configured()
                        else get_no_match_reply()
                    )
            except ChatbotAPIError:
                reply = get_off_topic_reply() if is_off else get_no_match_reply()
            resolved_intent = "off_topic" if is_off else "no_match"
            payload = {
                "reply": sanitize_bot_reply(reply),
                "articles": [],
                "primary_article": None,
                "has_trak_article": False,
                "intent": resolved_intent,
                "powered_by": "gemini" if is_chatbot_configured() else "local",
            }
            _append_chatbot_history(request.user.pk, message, payload["reply"], None)
            return Response(payload)

        if not articles and not is_chatbot_configured():
            payload = {
                "reply": "No news data found yet. Run the scraper and then refresh the feed.",
                "articles": [],
                "primary_article": None,
                "has_trak_article": False,
            }
            _append_chatbot_history(request.user.pk, message, payload["reply"], None)
            return Response(payload)

        reply = ""
        if is_chatbot_configured():
            try:
                reply = generate_chatbot_reply(
                    message,
                    articles,
                    prior_messages,
                    intent=intent,
                    has_db_match=db_match,
                )
            except ChatbotConfigError:
                reply = fallback_reply(message, articles, primary=primary, intent=intent)
            except ChatbotAPIError as exc:
                return Response(
                    {"detail": f"TRAK AI is temporarily unavailable: {exc}"},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
        else:
            reply = fallback_reply(message, articles, primary=primary, intent=intent)

        if intent == "summarize" and articles:
            primary = primary or articles[0]
            db_match = True

        serialized = [serialize_chat_article(a) for a in articles[:5]]
        serialized = [a for a in serialized if a]
        primary_payload = serialize_chat_article(primary) if db_match else None

        if intent in ("headlines", "summarize"):
            linkable = serialized[:5] if intent == "summarize" else serialized[:3]
        else:
            linkable = []
            for art in articles[:3]:
                if has_strong_article_match(search_message, art):
                    row = serialize_chat_article(art)
                    if row:
                        linkable.append(row)

        reply = sanitize_bot_reply(reply)
        if intent == "summarize" and articles:
            reply = finalize_reply_with_article_cards(
                reply,
                linkable,
                intent=intent,
                source_articles=articles,
            )
        elif linkable:
            reply = finalize_reply_with_article_cards(
                reply,
                linkable,
                intent=intent,
            )

        payload = {
            "reply": reply,
            "articles": serialized,
            "primary_article": primary_payload,
            "related_articles": linkable,
            "has_trak_article": bool(primary_payload or linkable),
            "intent": intent,
            "powered_by": "gemini" if is_chatbot_configured() else "local",
        }
        _append_chatbot_history(
            request.user.pk,
            message,
            payload["reply"],
            primary_payload,
            related=serialized,
        )
        return Response(payload)


def _append_chatbot_history(
    user_id: int,
    user_text: str,
    bot_text: str,
    primary_article: dict | None,
    *,
    related: list[dict] | None = None,
) -> None:
    col = chatbot_history_collection()
    row = col.find_one({"user_id": user_id}) or {"user_id": user_id, "messages": []}
    messages = row.get("messages") or []
    messages.append({"role": "user", "text": user_text})
    top = primary_article or {}
    messages.append(
        {
            "role": "bot",
            "text": bot_text,
            "article_id": top.get("id"),
            "article_title": top.get("title"),
            "article_path": top.get("trak_path"),
            "source": top.get("source"),
            "related_articles": related or [],
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
        article = article_query.get_article_by_id(article_id, request.user)
        if article is None:
            return Response({"detail": "Article not found."}, status=status.HTTP_404_NOT_FOUND)
        payload = {
            "user_id": request.user.pk,
            "article_id": article_id,
            "title": str(request.data.get("title") or article.get("title") or "").strip(),
            "url": str(
                request.data.get("url") or article.get("canonical_url") or article.get("url") or ""
            ).strip(),
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
