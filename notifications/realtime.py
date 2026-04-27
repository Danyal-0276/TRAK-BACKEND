from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def fanout_notification(user_id: int, notification: dict) -> None:
    layer = get_channel_layer()
    if not layer:
        return
    async_to_sync(layer.group_send)(
        f"user_notifications_{user_id}",
        {"type": "notify", "notification": notification},
    )
