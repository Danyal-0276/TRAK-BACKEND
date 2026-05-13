from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def fanout_notification(user_id: int | str, notification: dict) -> None:
    layer = get_channel_layer()
    if layer:
        async_to_sync(layer.group_send)(
            f"user_notifications_{user_id}",
            {"type": "notify", "notification": notification},
        )
    try:
        from notifications.fcm import send_fcm_to_user

        text = str(notification.get("text") or notification.get("message") or "").strip()
        title = str(notification.get("type") or "TRAK")
        send_fcm_to_user(
            user_id,
            title=title[:80] or "TRAK",
            body=text[:500] or "You have a new notification.",
            data={k: str(v) for k, v in notification.items() if k in ("id", "type", "text", "details")},
        )
    except Exception:
        pass
