"""Multi-session chatbot conversation storage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId

from news.mongo_db import chatbot_conversations_collection, chatbot_history_collection

MAX_MESSAGES_PER_CONVERSATION = 50
MAX_CONVERSATIONS_PER_USER = 100
DEFAULT_TITLE = "New chat"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _last_message_at(messages: list[dict]) -> datetime | Any | None:
    for msg in reversed(messages):
        ts = msg.get("created_at")
        if ts is not None:
            return ts
    return None


def _conversation_updated_at(row: dict, messages: list[dict]) -> str | None:
    updated = _last_message_at(messages) or row.get("updated_at") or row.get("created_at")
    return _to_iso(updated)


def _title_from_message(text: str, *, max_len: int = 48) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return DEFAULT_TITLE
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def _serialize_conversation(row: dict, *, include_messages: bool = False) -> dict:
    messages = row.get("messages") or []
    preview = ""
    for msg in reversed(messages):
        if str(msg.get("role") or "") == "user" and str(msg.get("text") or "").strip():
            preview = str(msg.get("text") or "").strip()
            break
    if not preview:
        for msg in reversed(messages):
            if str(msg.get("text") or "").strip():
                preview = str(msg.get("text") or "").strip()
                break
    if len(preview) > 80:
        preview = preview[:79].rstrip() + "…"

    updated = _conversation_updated_at(row, messages)
    created = _to_iso(row.get("created_at")) or updated
    payload: dict[str, Any] = {
        "id": str(row.get("_id") or ""),
        "title": str(row.get("title") or DEFAULT_TITLE),
        "created_at": created,
        "updated_at": updated,
        "preview": preview,
        "message_count": len(messages),
    }
    if include_messages:
        payload["messages"] = messages
    return payload


def _user_id_match(user_id: int) -> dict:
    """Match int or str user_id (older rows may differ)."""
    uid = int(user_id)
    return {"user_id": {"$in": [uid, str(uid)]}}


def _sessions_from_legacy_messages(messages: list[dict]) -> list[list[dict]]:
    """Split a flat legacy transcript into one session per user turn (+ following bot replies)."""
    sessions: list[list[dict]] = []
    current: list[dict] = []
    for msg in messages:
        role = str(msg.get("role") or "").lower()
        if role == "user":
            if current:
                sessions.append(current)
            current = [msg]
        else:
            if current:
                current.append(msg)
            elif sessions:
                sessions[-1].append(msg)
            else:
                current = [msg]
    if current:
        sessions.append(current)
    return [s for s in sessions if s]


def _insert_legacy_sessions(user_id: int, messages: list[dict], *, legacy_split: bool = False) -> None:
    col = chatbot_conversations_collection()
    sessions = _sessions_from_legacy_messages(messages)
    if not sessions:
        return
    now = _now()
    for session in sessions:
        title = DEFAULT_TITLE
        for msg in session:
            if str(msg.get("role") or "") == "user" and str(msg.get("text") or "").strip():
                title = _title_from_message(str(msg.get("text") or ""))
                break
        col.insert_one(
            {
                "user_id": int(user_id),
                "title": title,
                "messages": session[-MAX_MESSAGES_PER_CONVERSATION:],
                "created_at": now,
                "updated_at": now,
                "legacy_import": True,
                "legacy_split": legacy_split,
            }
        )


def migrate_legacy_history(user_id: int) -> None:
    """Import legacy single-thread history as one sidebar row per user turn (once)."""
    legacy = chatbot_history_collection().find_one({"user_id": int(user_id)}) or {}
    if not legacy:
        legacy = chatbot_history_collection().find_one({"user_id": str(user_id)}) or {}
    messages = legacy.get("messages") or []
    if not messages:
        return

    col = chatbot_conversations_collection()
    if col.find_one({**_user_id_match(user_id), "legacy_import": True}):
        return

    _insert_legacy_sessions(user_id, messages)
    chatbot_history_collection().update_one(
        {"user_id": legacy.get("user_id", int(user_id))},
        {"$set": {"messages": []}},
    )


def resplit_legacy_import_if_needed(user_id: int) -> None:
    """
    Older imports stored the full legacy thread in one conversation.
    Split into one sidebar row per user turn so history lists every exchange.
    """
    col = chatbot_conversations_collection()
    row = col.find_one({**_user_id_match(user_id), "legacy_import": True, "legacy_split": {"$ne": True}})
    if not row:
        return
    messages = list(row.get("messages") or [])
    if len(messages) <= 4:
        return
    sessions = _sessions_from_legacy_messages(messages)
    if len(sessions) <= 1:
        return
    col.delete_one({"_id": row["_id"]})
    _insert_legacy_sessions(user_id, messages, legacy_split=True)


def list_conversations(user_id: int) -> list[dict]:
    migrate_legacy_history(user_id)
    resplit_legacy_import_if_needed(user_id)
    col = chatbot_conversations_collection()
    rows = (
        col.find(_user_id_match(user_id))
        .sort([("updated_at", -1), ("_id", -1)])
        .limit(MAX_CONVERSATIONS_PER_USER)
    )
    return [_serialize_conversation(row) for row in rows]


def get_conversation(user_id: int, conversation_id: str) -> dict | None:
    oid = _parse_oid(conversation_id)
    if not oid:
        return None
    row = chatbot_conversations_collection().find_one({"_id": oid, **_user_id_match(user_id)})
    if not row:
        return None
    return _serialize_conversation(row, include_messages=True)


def delete_conversation(user_id: int, conversation_id: str) -> bool:
    oid = _parse_oid(conversation_id)
    if not oid:
        return False
    result = chatbot_conversations_collection().delete_one({"_id": oid, **_user_id_match(user_id)})
    return result.deleted_count > 0


def get_prior_messages(user_id: int, conversation_id: str | None) -> tuple[list[dict], str | None]:
    """Return prior messages and resolved conversation id."""
    if not conversation_id:
        return [], None
    oid = _parse_oid(conversation_id)
    if not oid:
        return [], None
    row = chatbot_conversations_collection().find_one({"_id": oid, **_user_id_match(user_id)})
    if not row:
        return [], None
    return list(row.get("messages") or []), str(row.get("_id"))


def append_conversation_exchange(
    user_id: int,
    conversation_id: str | None,
    user_text: str,
    bot_text: str,
    primary_article: dict | None,
    *,
    related: list[dict] | None = None,
) -> str:
    col = chatbot_conversations_collection()
    now = _now()
    oid = _parse_oid(conversation_id) if conversation_id else None
    row = col.find_one({"_id": oid, **_user_id_match(user_id)}) if oid else None

    if not row:
        title = _title_from_message(user_text)
        insert = col.insert_one(
            {
                "user_id": int(user_id),
                "title": title,
                "messages": [],
                "created_at": now,
                "updated_at": now,
            }
        )
        oid = insert.inserted_id
        row = col.find_one({"_id": oid}) or {"messages": [], "title": title}

    messages = list(row.get("messages") or [])
    messages.append({"role": "user", "text": user_text, "created_at": now})
    top = primary_article or {}
    messages.append(
        {
            "role": "bot",
            "text": bot_text,
            "created_at": now,
            "article_id": top.get("id"),
            "article_title": top.get("title"),
            "article_path": top.get("trak_path"),
            "source": top.get("source"),
            "related_articles": related or [],
        }
    )
    messages = messages[-MAX_MESSAGES_PER_CONVERSATION:]

    title = str(row.get("title") or DEFAULT_TITLE)
    if title == DEFAULT_TITLE:
        title = _title_from_message(user_text)

    col.update_one(
        {"_id": oid},
        {"$set": {"messages": messages, "title": title, "updated_at": now}},
    )
    _trim_old_conversations(user_id)
    return str(oid)


def _trim_old_conversations(user_id: int) -> None:
    col = chatbot_conversations_collection()
    rows = list(col.find(_user_id_match(user_id), {"_id": 1}).sort([("updated_at", -1), ("_id", -1)]))
    if len(rows) <= MAX_CONVERSATIONS_PER_USER:
        return
    stale_ids = [row["_id"] for row in rows[MAX_CONVERSATIONS_PER_USER :]]
    if stale_ids:
        col.delete_many({"_id": {"$in": stale_ids}, **_user_id_match(user_id)})


def _parse_oid(value: str | None) -> ObjectId | None:
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError, ValueError):
        return None
