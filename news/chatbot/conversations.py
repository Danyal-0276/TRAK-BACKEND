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

    updated = row.get("updated_at") or row.get("created_at")
    payload: dict[str, Any] = {
        "id": str(row.get("_id") or ""),
        "title": str(row.get("title") or DEFAULT_TITLE),
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else updated,
        "preview": preview,
        "message_count": len(messages),
    }
    if include_messages:
        payload["messages"] = messages
    return payload


def migrate_legacy_history(user_id: int) -> None:
    """Import legacy single-thread history as one conversation (once)."""
    legacy = chatbot_history_collection().find_one({"user_id": user_id}) or {}
    messages = legacy.get("messages") or []
    if not messages:
        return

    col = chatbot_conversations_collection()
    if col.find_one({"user_id": user_id, "legacy_import": True}):
        return

    title = DEFAULT_TITLE
    for msg in messages:
        if str(msg.get("role") or "") == "user" and str(msg.get("text") or "").strip():
            title = _title_from_message(str(msg.get("text") or ""))
            break
    if title == DEFAULT_TITLE:
        title = "Previous chat"

    now = _now()
    col.insert_one(
        {
            "user_id": user_id,
            "title": title,
            "messages": messages[-MAX_MESSAGES_PER_CONVERSATION:],
            "created_at": now,
            "updated_at": now,
            "legacy_import": True,
        }
    )
    chatbot_history_collection().update_one({"user_id": user_id}, {"$set": {"messages": []}})


def list_conversations(user_id: int) -> list[dict]:
    migrate_legacy_history(user_id)
    col = chatbot_conversations_collection()
    rows = col.find({"user_id": user_id}).sort("updated_at", -1).limit(MAX_CONVERSATIONS_PER_USER)
    return [_serialize_conversation(row) for row in rows]


def get_conversation(user_id: int, conversation_id: str) -> dict | None:
    oid = _parse_oid(conversation_id)
    if not oid:
        return None
    row = chatbot_conversations_collection().find_one({"_id": oid, "user_id": user_id})
    if not row:
        return None
    return _serialize_conversation(row, include_messages=True)


def delete_conversation(user_id: int, conversation_id: str) -> bool:
    oid = _parse_oid(conversation_id)
    if not oid:
        return False
    result = chatbot_conversations_collection().delete_one({"_id": oid, "user_id": user_id})
    return result.deleted_count > 0


def get_prior_messages(user_id: int, conversation_id: str | None) -> tuple[list[dict], str | None]:
    """Return prior messages and resolved conversation id."""
    if not conversation_id:
        return [], None
    oid = _parse_oid(conversation_id)
    if not oid:
        return [], None
    row = chatbot_conversations_collection().find_one({"_id": oid, "user_id": user_id})
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
    row = col.find_one({"_id": oid, "user_id": user_id}) if oid else None

    if not row:
        title = _title_from_message(user_text)
        insert = col.insert_one(
            {
                "user_id": user_id,
                "title": title,
                "messages": [],
                "created_at": now,
                "updated_at": now,
            }
        )
        oid = insert.inserted_id
        row = col.find_one({"_id": oid}) or {"messages": [], "title": title}

    messages = list(row.get("messages") or [])
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
    rows = list(col.find({"user_id": user_id}, {"_id": 1}).sort("updated_at", -1))
    if len(rows) <= MAX_CONVERSATIONS_PER_USER:
        return
    stale_ids = [row["_id"] for row in rows[MAX_CONVERSATIONS_PER_USER :]]
    if stale_ids:
        col.delete_many({"_id": {"$in": stale_ids}, "user_id": user_id})


def _parse_oid(value: str | None) -> ObjectId | None:
    if not value:
        return None
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError, ValueError):
        return None
