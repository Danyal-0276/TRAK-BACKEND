"""Send password reset email (Django token + uid) and OTP reset-session tokens."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

User = get_user_model()


def build_reset_url(user: User) -> tuple[str, str, str]:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    base = getattr(settings, "PASSWORD_RESET_FRONTEND_URL", "").rstrip("/")
    if not base:
        base = "http://127.0.0.1:5173/reset-password"
    reset_url = f"{base}?uid={uid}&token={token}"
    return reset_url, uid, token


PWRESET_TOKEN_CACHE_PREFIX = "auth:pwreset:token:"
PWRESET_TOKEN_TTL_SECONDS = 900  # 15 minutes


def issue_password_reset_session(email: str) -> str:
    """Short-lived token after OTP verify; required to set a new password."""
    token = secrets.token_urlsafe(32)
    cache.set(
        f"{PWRESET_TOKEN_CACHE_PREFIX}{token}",
        email.strip().lower(),
        timeout=PWRESET_TOKEN_TTL_SECONDS,
    )
    return token


def consume_password_reset_session(*, email: str, reset_token: str) -> bool:
    """Validate and invalidate reset token for this email."""
    key = f"{PWRESET_TOKEN_CACHE_PREFIX}{(reset_token or '').strip()}"
    cached = cache.get(key)
    if not cached or cached != email.strip().lower():
        return False
    cache.delete(key)
    return True


def send_password_reset_email(user: User) -> None:
    reset_url, _uid, _token = build_reset_url(user)
    subject = "Reset your TRAK password"
    body = (
        "Hi,\n\n"
        "We received a request to reset the password for your TRAK account.\n"
        "Open this link to choose a new password (it expires after a while):\n\n"
        f"{reset_url}\n\n"
        "If you did not ask for this, you can ignore this email.\n"
    )
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )
