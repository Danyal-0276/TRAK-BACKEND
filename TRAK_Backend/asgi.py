"""
ASGI config for TRAK_Backend project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/3.2/howto/deployment/asgi/
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TRAK_Backend.settings")

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

# Initialize Django before importing modules that touch auth/models.
django_asgi_app = get_asgi_application()
from notifications.routing import websocket_urlpatterns
from notifications.ws_auth import QueryStringJWTAuthMiddleware

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": QueryStringJWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)
