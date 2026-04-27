from __future__ import annotations

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import UntypedToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.authentication import JWTAuthentication

User = get_user_model()


@database_sync_to_async
def _get_user(token: str):
    try:
        UntypedToken(token)
    except (InvalidToken, TokenError):
        return None
    auth = JWTAuthentication()
    validated = auth.get_validated_token(token)
    user_id = validated.get("user_id")
    if not user_id:
        return None
    return User.objects.filter(pk=user_id).first()


class QueryStringJWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query = parse_qs(scope.get("query_string", b"").decode("utf-8"))
        token = (query.get("token") or [""])[0]
        scope["user"] = await _get_user(token) if token else None
        return await super().__call__(scope, receive, send)
