from django.urls import re_path

from .consumers import AdminNotificationsConsumer, NotificationsConsumer

websocket_urlpatterns = [
    re_path(r"ws/notifications/$", NotificationsConsumer.as_asgi()),
    re_path(r"ws/admin/notifications/$", AdminNotificationsConsumer.as_asgi()),
]
