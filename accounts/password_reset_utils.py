"""Send password reset email (Django token + uid)."""

from __future__ import annotations

from django.conf import settings
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
