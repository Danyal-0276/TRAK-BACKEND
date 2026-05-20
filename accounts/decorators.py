"""django-ratelimit integration for DRF APIView classes."""

from __future__ import annotations

import logging

from django.conf import settings
from django_ratelimit.core import is_ratelimited
from rest_framework import status
from rest_framework.response import Response

logger = logging.getLogger("accounts.security")


def _ratelimit_enabled() -> bool:
    return getattr(settings, "RATELIMIT_ENABLE", True)


class RatelimitedAPIMixin:
    """
    IP-based rate limits via django-ratelimit (applied in dispatch, safe for DRF views).
    Set on the view class: ratelimit_key, ratelimit_rate, ratelimit_method.
    """

    ratelimit_key = "ip"
    ratelimit_rate = "30/m"
    ratelimit_method = "POST"
    ratelimit_group = None

    def dispatch(self, request, *args, **kwargs):
        if _ratelimit_enabled():
            group = self.ratelimit_group or self.__class__.__name__
            try:
                limited = is_ratelimited(
                    request,
                    group=group,
                    key=self.ratelimit_key,
                    rate=self.ratelimit_rate,
                    method=self.ratelimit_method,
                    increment=True,
                )
            except Exception:
                logger.warning(
                    "Rate limit cache unavailable for %s; continuing without django-ratelimit",
                    group,
                    exc_info=True,
                )
                limited = False
            if limited:
                return Response(
                    {"detail": "Too many requests. Please try again later."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
        return super().dispatch(request, *args, **kwargs)
