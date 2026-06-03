"""Proxy external article images for authenticated clients (hotlink / referrer fallback)."""

from __future__ import annotations

from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.http import HttpResponse
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView


class ArticleImageProxyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        raw_url = str(request.query_params.get("url") or "").strip()
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return Response({"detail": "Invalid image URL."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            req = Request(raw_url, headers={"User-Agent": "TRAK/1.0", "Accept": "image/*,*/*"})
            with urlopen(req, timeout=12) as remote:
                content_type = remote.headers.get("Content-Type") or "image/jpeg"
                if not str(content_type).lower().startswith("image/"):
                    return Response({"detail": "URL is not an image."}, status=status.HTTP_400_BAD_REQUEST)
                body = remote.read(5_000_000)
            response = HttpResponse(body, content_type=content_type)
            response["Cache-Control"] = "private, max-age=3600"
            return response
        except Exception as exc:
            return Response(
                {"detail": f"Could not fetch image: {exc}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
