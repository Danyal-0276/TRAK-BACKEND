from __future__ import annotations

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model

User = get_user_model()


class NotificationsConsumer(AsyncJsonWebsocketConsumer):
    """User app notifications websocket."""

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return
        if getattr(user, "role", None) == User.Role.ADMIN:
            await self.close(code=4403)
            return
        self.group_name = f"user_notifications_{user.pk}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"type": "connection.ack", "message": "connected", "audience": "user"})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notify(self, event):
        await self.send_json(
            {
                "type": "notification.created",
                "notification": event.get("notification") or {},
            }
        )


class AdminNotificationsConsumer(AsyncJsonWebsocketConsumer):
    """Admin panel live alerts (pipeline errors, system)."""

    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return
        if getattr(user, "role", None) != User.Role.ADMIN:
            await self.close(code=4403)
            return
        self.group_name = f"admin_notifications_{user.pk}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"type": "connection.ack", "message": "connected", "audience": "admin"})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notify(self, event):
        await self.send_json(
            {
                "type": "notification.created",
                "notification": event.get("notification") or {},
            }
        )
