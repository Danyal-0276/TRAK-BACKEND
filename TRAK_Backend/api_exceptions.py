"""DRF exception handler — avoids leaking internals when DEBUG is off."""

from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response
    if not settings.DEBUG:
        logger.exception("Unhandled exception in API view")
        return Response({"detail": "Server error."}, status=500)
    return None
