from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def _group_name(user_id: int | str, audience: str) -> str:
    if audience == "admin":
        return f"admin_notifications_{user_id}"
    return f"user_notifications_{user_id}"


def fanout_notification(
    user_id: int | str,
    notification: dict,
    *,
    audience: str = "user",
    send_push: bool = True,
) -> None:
    layer = get_channel_layer()
    group = _group_name(user_id, audience)
    if layer:
        async_to_sync(layer.group_send)(
            group,
            {"type": "notify", "notification": notification},
        )
    if not send_push:
        return
    try:
        from notifications.fcm import send_fcm_to_user

        text = str(notification.get("text") or notification.get("message") or "").strip()
        title = str(notification.get("title") or notification.get("type") or "TRAK")
        send_fcm_to_user(
            user_id,
            title=title[:80] or "TRAK",
            body=text[:500] or "You have a new notification.",
            data={k: str(v) for k, v in notification.items() if k in ("id", "type", "text", "details", "audience")},
        )
    except Exception:
        pass
