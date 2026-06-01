"""
TRAK news chatbot — Google Gemini 1.5 Flash.

Grounded in processed_articles (MongoDB). Only TRAK in-app article links.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from django.conf import settings
from django.contrib.auth import get_user_model

from news.chatbot.intents import (
    IDENTITY_REPLY,
    NO_MATCH_REPLY,
    OFF_TOPIC_REPLY,
    article_matches_terms,
    article_relevance_score,
    build_search_query,
    detect_intent,
    extract_search_terms,
    classify_empty_result,
    filter_relevant_articles,
    has_news_intent,
    is_off_topic_message,
    should_link_article,
)
from news.services import article_query

logger = logging.getLogger(__name__)
User = get_user_model()

_EXTERNAL_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

SYSTEM_INSTRUCTION = """You are TRAK AI Assistant inside the TRAK news application.

Identity: Built by the TRAK team. If asked who made you, say the TRAK team.

Rules:
1. Use ONLY the TRAK article context provided in each user turn.
2. NEVER output http:// or https:// links or tell users to visit external websites.
3. When TRAK has matching articles, the app shows article cards below your message — do NOT repeat article titles, sources, or long summaries in your text.
4. For matching articles: write at most 1-2 short sentences with a thematic overview only (e.g. what the stories are about). The UI already introduces them as "articles I found".
5. If no relevant articles in context, say TRAK does not have that story yet; do not claim loose matches.
6. If the user asks about coding, homework, recipes, jokes, or other non-news topics, say you only help with news and suggest a news question — do not invent related articles.
7. Plain text, friendly, concise. No markdown headings.
"""

OFF_TOPIC_GEMINI_INSTRUCTION = """You are TRAK AI in the TRAK news app, created by the TRAK team.

The user's message is NOT a news question. Reply in natural, friendly language.

You MUST:
- Explain that you only help with news, headlines, and stories in TRAK
- Politely refuse to do what they asked (essays, code, homework, recipes, jokes, general tasks, etc.)
- NOT claim TRAK has articles on their topic and NOT suggest article cards
- Give one short example of a news question they could ask instead

You MUST NOT:
- Write the essay, code, recipe, or other content they requested
- Include http:// or https:// links

Keep it to 2-4 sentences. Plain text only."""

IDENTITY_GEMINI_INSTRUCTION = """You are TRAK AI, built by the TRAK team for the TRAK news application.

The user is asking about you. In 2-3 friendly sentences:
- Say the TRAK team built you to help users explore news in TRAK
- Do not credit Google, Gemini, or OpenAI
- Invite them to ask a news question
Plain text, no URLs."""


class ChatbotConfigError(Exception):
    pass


class ChatbotAPIError(Exception):
    pass


def is_chatbot_configured() -> bool:
    return bool(getattr(settings, "GEMINI_API_KEY", None))


def get_identity_reply() -> str:
    return IDENTITY_REPLY


def get_off_topic_reply() -> str:
    return OFF_TOPIC_REPLY


def get_no_match_reply() -> str:
    return NO_MATCH_REPLY


def trak_article_path(article_id: str | None) -> Optional[str]:
    aid = str(article_id or "").strip()
    return f"/article/{aid}" if aid else None


def serialize_chat_article(article: dict | None) -> Optional[dict]:
    if not article:
        return None
    aid = str(article.get("id") or "").strip()
    if not aid:
        return None
    return {
        "id": aid,
        "title": article.get("title") or "Untitled",
        "source": article.get("source") or "",
        "summary": (article.get("summary") or article.get("excerpt") or "")[:400],
        "trak_path": trak_article_path(aid),
    }


def gather_news_context(user: User, message: str, *, limit: int = 8) -> tuple[list[dict], str]:
    """
    Load articles from TRAK MongoDB.
    Returns (articles, intent) where intent includes off_topic when not news-related.
    """
    intent = detect_intent(message)
    if intent in ("identity", "off_topic"):
        return [], intent

    if intent == "summarize":
        search_q = build_search_query(message)
        seen: set[str] = set()
        merged: list[dict] = []

        def _add(batch: list[dict]) -> None:
            for art in batch:
                aid = str(art.get("id") or "").strip()
                if not aid or aid in seen:
                    continue
                seen.add(aid)
                merged.append(art)
                if len(merged) >= limit:
                    return

        if search_q:
            _add(article_query.search_processed_articles(search_q, limit=limit))
            if len(merged) < limit:
                _add(article_query.get_explore_feed(limit=limit, search_q=search_q))
            if len(merged) < limit:
                _add(article_query.get_user_feed(user, limit=limit, search_q=search_q))
        if len(merged) < limit and not is_off_topic_message(message):
            _add(article_query.get_recent_processed_articles(limit=limit))

        ranked = sorted(
            merged,
            key=lambda a: article_relevance_score(message, a),
            reverse=True,
        )
        relevant = filter_relevant_articles(message, ranked)
        if not relevant:
            return [], classify_empty_result(message, had_search_hits=bool(ranked))
        return relevant[:limit], intent

    if intent == "headlines":
        terms = extract_search_terms(message)
        recent = article_query.get_recent_processed_articles(limit=limit * 2)
        if terms:
            filtered = [a for a in recent if article_matches_terms(a, terms)]
            return (filtered or recent)[:limit], intent
        return recent[:limit], intent

    search_q = build_search_query(message)
    seen: set[str] = set()
    merged: list[dict] = []

    def _add(batch: list[dict]) -> None:
        for art in batch:
            aid = str(art.get("id") or "").strip()
            if not aid or aid in seen:
                continue
            seen.add(aid)
            merged.append(art)
            if len(merged) >= limit:
                return

    if search_q:
        _add(article_query.search_processed_articles(search_q, limit=limit))
    if len(merged) < limit and search_q:
        _add(article_query.get_explore_feed(limit=limit, search_q=search_q))
    if len(merged) < limit and search_q:
        _add(article_query.get_user_feed(user, limit=limit, search_q=search_q))

    ranked = sorted(
        merged,
        key=lambda a: article_relevance_score(message, a),
        reverse=True,
    )
    relevant = filter_relevant_articles(message, ranked)
    if not relevant:
        return [], classify_empty_result(message, had_search_hits=bool(ranked))
    return relevant[:limit], intent


def pick_primary_article(message: str, articles: list[dict]) -> Optional[dict]:
    if not articles:
        return None
    best: Optional[dict] = None
    best_score = 0.0
    for art in articles:
        score = article_relevance_score(message, art)
        if score > best_score:
            best_score = score
            best = art
    if best and best_score > 0:
        return best
    if detect_intent(message) in ("headlines", "summarize") and articles:
        return articles[0]
    return None


def has_strong_article_match(message: str, article: dict | None) -> bool:
    """True when the user should get an in-app TRAK article link."""
    if not article:
        return False
    if detect_intent(message) in ("headlines", "summarize"):
        return True
    return should_link_article(message, article)


def _format_articles_block(articles: list[dict], intent: str) -> str:
    if not articles:
        return "(No TRAK articles in the database for this request.)"
    blocks: list[str] = []
    for i, art in enumerate(articles, 1):
        title = str(art.get("title") or "Untitled").strip()
        source = str(art.get("source") or "unknown").strip()
        summary = str(art.get("summary") or art.get("excerpt") or "").strip()
        if len(summary) > 450:
            summary = summary[:447] + "..."
        blocks.append(
            f"[{i}] id={art.get('id')}\n"
            f"Title: {title}\n"
            f"Source: {source}\n"
            f"Summary: {summary or '(no summary)'}"
        )
    if intent == "headlines":
        header = "Recent TRAK headlines:"
    elif intent == "summarize":
        header = "TRAK articles to summarize:"
    else:
        header = "TRAK articles matching this topic:"
    return f"{header}\n\n" + "\n\n".join(blocks)


def _history_to_gemini(history: list[dict], *, max_turns: int = 5) -> list[dict[str, Any]]:
    trimmed = history[-(max_turns * 2) :]
    out: list[dict[str, Any]] = []
    for row in trimmed:
        role = "user" if row.get("role") == "user" else "model"
        text = str(row.get("text") or "").strip()
        if text:
            out.append({"role": role, "parts": [text]})
    return out


def sanitize_bot_reply(text: str) -> str:
    cleaned = _EXTERNAL_URL_RE.sub("", text or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def format_summarize_intro(count: int) -> str:
    n = max(1, int(count or 1))
    if n == 1:
        return (
            "Here's the TRAK article I used. "
            "Tap the card below for the full story."
        )
    return (
        f"Here are {n} related articles from TRAK. "
        "Tap any card below to read more."
    )


def build_local_summary_paragraph(articles: list[dict], *, max_chars: int = 650) -> str:
    """Fallback: combine article summaries into one short paragraph."""
    if not articles:
        return "TRAK does not have enough content to summarize that topic yet."

    sentences: list[str] = []
    for art in articles[:4]:
        summary = str(art.get("summary") or art.get("excerpt") or "").strip()
        if summary:
            first = re.split(r"(?<=[.!?])\s+", summary)[0].strip()
            if first and first not in sentences:
                sentences.append(first.rstrip(".") + ".")
            continue
        title = str(art.get("title") or "").strip()
        if title:
            sentences.append(f"Coverage includes {title.rstrip('.')}.")

    if not sentences:
        return "These stories are available in TRAK — open the cards below for full details."

    paragraph = " ".join(sentences)
    if len(paragraph) > max_chars:
        paragraph = paragraph[: max_chars - 3].rsplit(" ", 1)[0] + "..."
    return paragraph


def format_related_articles_intro(count: int, *, intent: str = "search") -> str:
    """Short message when article cards are shown below (no titles duplicated)."""
    n = max(1, int(count or 1))
    if intent == "summarize":
        return format_summarize_intro(n)
    if intent == "headlines":
        if n == 1:
            return (
                "Here's a recent headline I found in TRAK. "
                "Tap the card below to read the full article."
            )
        return (
            f"Here are {n} recent headlines I found in TRAK. "
            "Tap any card below to read the full article."
        )
    if n == 1:
        return (
            "Here's an article I found in TRAK that's related to your question. "
            "Tap the card below to read it."
        )
    return (
        f"Here are {n} articles I found in TRAK related to your question. "
        "Tap any card below to read the full story."
    )


def _titles_in_linkable(linkable: list[dict]) -> list[str]:
    titles: list[str] = []
    for row in linkable:
        t = str(row.get("title") or "").strip()
        if len(t) > 12:
            titles.append(t)
    return titles


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_card_intro_text(text: str) -> bool:
    """True when the message is already the standard TRAK card intro."""
    low = _normalize_text(text)
    if "found in trak" not in low:
        return False
    return ("tap" in low and "card" in low) or "tap the card below" in low


def _dedupe_paragraphs(text: str) -> str:
    """Remove repeated paragraphs (e.g. intro pasted twice)."""
    parts = [p.strip() for p in re.split(r"\n\n+", (text or "").strip()) if p.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        key = _normalize_text(part)
        if key and key not in seen:
            seen.add(key)
            out.append(part)
    return "\n\n".join(out)


def _extract_short_insight(reply: str, linkable: list[dict]) -> str:
    """Keep a brief Gemini line if it does not repeat card titles or the card intro."""
    text = (reply or "").strip()
    if not text or _is_card_intro_text(text):
        return ""

    boilerplate = (
        "We have this in TRAK",
        "Tap the article card below to read the full story",
        "Tap the article card below",
        "Tap any card below to read the full story",
        "Open the article card",
    )
    for title in _titles_in_linkable(linkable):
        if title.lower() in text.lower():
            text = re.sub(re.escape(title), "", text, flags=re.IGNORECASE)

    for phrase in boilerplate:
        text = re.sub(re.escape(phrase) + r"[^.!?]*[.!?]?", "", text, flags=re.IGNORECASE)

    text = re.sub(r"\s+", " ", text).strip(" .")
    if len(text) < 15 or len(text) > 220:
        return ""
    return text


def _extract_summary_paragraph(reply: str, linkable: list[dict]) -> str:
    """Pull the summary body from Gemini (may be multiple paragraphs)."""
    text = _dedupe_paragraphs((reply or "").strip())
    if not text or _is_card_intro_text(text):
        return ""

    parts = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    body_parts: list[str] = []
    for part in parts:
        if _is_card_intro_text(part):
            continue
        low = part.lower()
        if any(t.lower() in low for t in _titles_in_linkable(linkable) if len(t) > 20):
            continue
        body_parts.append(part)

    body = "\n\n".join(body_parts).strip()
    body = re.sub(r"\s+", " ", body) if len(body) < 40 else body
    if len(body) < 40:
        return ""
    if len(body) > 900:
        body = body[:897].rsplit(" ", 1)[0] + "..."
    return body


def finalize_summarize_reply(
    reply: str,
    linkable: list[dict],
    articles: list[dict],
) -> str:
    """Intro + one summary paragraph; cards hold titles."""
    intro = format_summarize_intro(len(linkable) if linkable else len(articles))
    summary = _extract_summary_paragraph(reply, linkable or articles)
    if not summary:
        summary = build_local_summary_paragraph(articles)
    return f"{intro}\n\n{summary}"


def finalize_reply_with_article_cards(
    reply: str,
    linkable: list[dict],
    *,
    intent: str = "search",
    source_articles: list[dict] | None = None,
) -> str:
    if intent == "summarize":
        return finalize_summarize_reply(
            reply,
            linkable,
            source_articles or linkable,
        )
    """
    When the client shows article cards, use a clean intro and optional short insight —
    never repeat titles/summaries that already appear on the cards.
    """
    if not linkable:
        return reply

    intro = format_related_articles_intro(len(linkable), intent=intent)
    cleaned = _dedupe_paragraphs((reply or "").strip())

    if not cleaned or _normalize_text(cleaned) == _normalize_text(intro):
        return intro

    if _is_card_intro_text(cleaned):
        return cleaned

    insight = _extract_short_insight(reply, linkable)
    if insight and _normalize_text(insight) != _normalize_text(intro):
        return f"{intro}\n\n{insight}"

    return intro


def _gemini_models_to_try() -> list[str]:
    configured = getattr(settings, "GEMINI_CHATBOT_MODEL", "gemini-1.5-flash") or "gemini-1.5-flash"
    fallbacks = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-8b"]
    out: list[str] = []
    for name in [configured, *fallbacks]:
        if name and name not in out:
            out.append(name)
    return out


def _run_gemini_reply(
    user_message: str,
    *,
    system_instruction: str,
    user_prompt: str,
    history: list[dict] | None,
    fallback: str,
) -> str:
    """Single-turn or chat Gemini call with static fallback when unavailable."""
    if not is_chatbot_configured():
        return fallback

    api_key = getattr(settings, "GEMINI_API_KEY", "") or ""
    try:
        import google.generativeai as genai
    except ImportError:
        return fallback

    genai.configure(api_key=api_key)
    gemini_history = _history_to_gemini(history or [])
    last_error: Exception | None = None

    for model_name in _gemini_models_to_try():
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_instruction,
            )
            if gemini_history:
                chat = model.start_chat(history=gemini_history)
                response = chat.send_message(user_prompt)
            else:
                response = model.generate_content(user_prompt)
            text = sanitize_bot_reply((response.text or "").strip())
            if text:
                return text
            last_error = ChatbotAPIError("Empty response from Gemini")
        except Exception as exc:
            logger.warning("Gemini (%s) failed: %s", model_name, exc)
            last_error = exc

    logger.warning("Gemini decline fallback used: %s", last_error)
    return fallback


def generate_off_topic_reply(
    user_message: str,
    history: list[dict] | None = None,
) -> str:
    """Gemini-generated polite decline for non-news requests (no articles)."""
    prompt = (
        f"The user said:\n{user_message.strip()}\n\n"
        "Write your refusal and redirect them to a news question."
    )
    return _run_gemini_reply(
        user_message,
        system_instruction=OFF_TOPIC_GEMINI_INSTRUCTION,
        user_prompt=prompt,
        history=history,
        fallback=get_off_topic_reply(),
    )


def generate_identity_reply(
    user_message: str,
    history: list[dict] | None = None,
) -> str:
    """Gemini-generated identity answer (TRAK team)."""
    prompt = f"User question:\n{user_message.strip()}"
    return _run_gemini_reply(
        user_message,
        system_instruction=IDENTITY_GEMINI_INSTRUCTION,
        user_prompt=prompt,
        history=history,
        fallback=get_identity_reply(),
    )


def generate_no_match_reply(
    user_message: str,
    history: list[dict] | None = None,
) -> str:
    """Gemini reply when TRAK has no articles for a valid news question."""
    prompt = (
        f"The user asked a news question but TRAK has no matching articles in the database:\n"
        f"{user_message.strip()}\n\n"
        "Say TRAK does not have that story yet. Suggest different keywords or the feed. "
        "Do not invent articles. 2-3 sentences. No URLs."
    )
    instruction = (
        SYSTEM_INSTRUCTION
        + "\n\nNo article context is available for this turn. Do not mention specific headlines."
    )
    return _run_gemini_reply(
        user_message,
        system_instruction=instruction,
        user_prompt=prompt,
        history=history,
        fallback=get_no_match_reply(),
    )


def generate_chatbot_reply(
    user_message: str,
    articles: list[dict],
    history: list[dict] | None = None,
    *,
    intent: str = "search",
    has_db_match: bool = False,
) -> str:
    api_key = getattr(settings, "GEMINI_API_KEY", "") or ""
    if not api_key:
        raise ChatbotConfigError("GEMINI_API_KEY is not set")

    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise ChatbotConfigError("google-generativeai package is not installed") from exc

    genai.configure(api_key=api_key)

    context_block = _format_articles_block(articles, intent)
    if intent == "summarize":
        match_note = (
            "The user wants a SUMMARY. Write exactly ONE short paragraph (4–6 sentences) "
            "that synthesizes the main news across ALL articles above. "
            "Cover key facts and themes only from the provided text. "
            "Do NOT list article titles or sources. Do NOT mention tapping cards or TRAK UI. "
            "Do NOT include URLs. Output only the summary paragraph."
        )
    elif intent == "headlines":
        match_note = (
            "Headline request. Write 1 short sentence about the overall theme only. "
            "Do NOT list titles — cards appear below."
        )
    elif has_db_match and articles:
        match_note = (
            "Strong TRAK matches exist. Write 1-2 sentences: thematic overview only. "
            "Do NOT repeat titles, sources, or summaries — article cards show below."
        )
    elif articles:
        match_note = (
            "Loose matches only. Say they may not be exact, 1-2 sentences max, no titles."
        )
    else:
        match_note = "No TRAK articles — say TRAK does not have this story yet."

    user_prompt = (
        f"{context_block}\n\n"
        f"Instructions: {match_note}\n\n"
        f"User: {user_message.strip()}"
    )

    gemini_history = _history_to_gemini(history or [])
    last_error: Exception | None = None

    for model_name in _gemini_models_to_try():
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=SYSTEM_INSTRUCTION,
            )
            chat = model.start_chat(history=gemini_history)
            response = chat.send_message(user_prompt)
            text = sanitize_bot_reply((response.text or "").strip())
            if text:
                return text
            last_error = ChatbotAPIError("Empty response from Gemini")
        except Exception as exc:
            logger.warning("Gemini model %s failed: %s", model_name, exc)
            last_error = exc

    raise ChatbotAPIError(str(last_error or "Gemini request failed"))


def _linkable_from_articles(
    message: str,
    articles: list[dict],
    *,
    intent: str,
    limit: int = 3,
) -> list[dict]:
    if intent == "headlines":
        return articles[:limit]
    out: list[dict] = []
    for art in articles[:limit]:
        if should_link_article(message, art):
            out.append(art)
    return out


def fallback_reply(
    message: str,
    articles: list[dict],
    *,
    primary: dict | None = None,
    intent: str = "search",
) -> str:
    if intent == "identity":
        return get_identity_reply()

    if intent == "off_topic":
        return get_off_topic_reply()

    if intent == "no_match":
        return get_no_match_reply()

    if intent == "summarize":
        if articles:
            return build_local_summary_paragraph(articles)
        return (
            "I couldn't find TRAK articles to summarize for that topic yet. "
            "Try another keyword or check your feed."
        )

    linkable = _linkable_from_articles(message, articles, intent=intent)
    if linkable:
        return format_related_articles_intro(len(linkable), intent=intent)

    if intent == "headlines" and articles:
        return format_related_articles_intro(min(len(articles), 3), intent="headlines")

    return get_no_match_reply()
