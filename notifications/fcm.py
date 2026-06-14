"""Optional Firebase Cloud Messaging fan-out.

Set FIREBASE_CREDENTIALS_JSON to the full service-account JSON string, or set
GOOGLE_APPLICATION_CREDENTIALS to a path to the JSON file. If neither is set,
push is skipped (Channels/WebSocket still deliver).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# None = not tried yet; app instance = ready; False = push disabled (log once).
_app: Any = None
_fcm_unavailable_logged = False


def _log_fcm_unavailable_once(message: str, *args) -> None:
    global _fcm_unavailable_logged
    if _fcm_unavailable_logged:
        return
    _fcm_unavailable_logged = True
    logger.info("FCM push disabled: " + message, *args)


def _ensure_app():
    global _app
    if _app is False:
        return None
    if _app is not None:
        return _app
    raw = (getattr(settings, "FIREBASE_CREDENTIALS_JSON", None) or "").strip()
    path = (getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", None) or "").strip()
    if not raw and not path:
        _app = False
        return None
    if path and not os.path.isfile(path):
        _app = False
        _log_fcm_unavailable_once(
            "credentials file not found at %s (in-app notifications still work).",
            path,
        )
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials

        if firebase_admin._apps:
            _app = firebase_admin.get_app()
            return _app
        if raw:
            cred = credentials.Certificate(json.loads(raw))
        else:
            cred = credentials.Certificate(path)
        _app = firebase_admin.initialize_app(cred)
        logger.info("FCM push enabled.")
        return _app
    except Exception as exc:
        _app = False
        _log_fcm_unavailable_once("%s", exc)
        return None


def _user_id_query_variants(user_id: Any) -> list:
    variants = {user_id}
    try:
        if isinstance(user_id, str) and user_id.isdigit():
            variants.add(int(user_id))
    except Exception:
        pass
    try:
        if isinstance(user_id, int):
            variants.add(str(user_id))
    except Exception:
        pass
    return list(variants)


def send_fcm_to_user(user_id: Any, title: str, body: str, data: dict | None = None) -> None:
    if not _ensure_app():
        return
    try:
        from firebase_admin import messaging

        from news.mongo_db import device_tokens_collection

        uids = _user_id_query_variants(user_id)
        coll = device_tokens_collection()
        tokens: list[str] = []
        for doc in coll.find({"user_id": {"$in": uids}}):
            t = doc.get("token")
            if t and isinstance(t, str) and t.strip():
                tokens.append(t.strip())
        if not tokens:
            return
        data = data or {}
        str_data = {str(k): str(v) for k, v in data.items() if v is not None}
        android_cfg = None
        if str_data.get("type") == "keyword_match":
            android_cfg = messaging.AndroidConfig(priority="high")
        # FCM multicast batches (max 500)
        for i in range(0, len(tokens), 500):
            batch = tokens[i : i + 500]
            msg = messaging.MulticastMessage(
                notification=messaging.Notification(title=title or "TRAK", body=body or ""),
                data=str_data,
                tokens=batch,
                android=android_cfg,
            )
            send_fn = getattr(messaging, "send_each_for_multicast", None) or getattr(messaging, "send_multicast", None)
            if not send_fn:
                continue
            resp = send_fn(msg)
            _prune_invalid_tokens(coll, batch, resp)
    except Exception as exc:
        logger.warning("FCM send failed: %s", exc)


def _prune_invalid_tokens(coll, tokens: list[str], resp) -> None:
    """Drop expired/invalid FCM tokens so users don't get duplicate retries."""
    responses = getattr(resp, "responses", None)
    if not responses:
        return
    for idx, result in enumerate(responses):
        if result.success:
            continue
        exc = result.exception
        if exc is None:
            continue
        msg = str(exc).lower()
        if "not found" in msg or "invalid" in msg or "unregistered" in msg:
            try:
                coll.delete_one({"token": tokens[idx]})
            except Exception:
                pass
