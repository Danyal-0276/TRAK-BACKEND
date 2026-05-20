"""Transactional auth emails with HTML templates."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger("accounts.email")


class AuthEmailService:
    @staticmethod
    def send_otp_email(*, to_email: str, code: str, purpose_label: str, expires_minutes: int) -> None:
        subject = f"Your TRAK {purpose_label} code"
        context = {
            "code": code,
            "purpose_label": purpose_label,
            "expires_minutes": expires_minutes,
            "app_name": "TRAK",
        }
        text_body = render_to_string("accounts/emails/otp_email.txt", context)
        html_body = render_to_string("accounts/emails/otp_email.html", context)
        from_email = settings.DEFAULT_FROM_EMAIL

        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[to_email],
        )
        message.attach_alternative(html_body, "text/html")
        try:
            message.send(fail_silently=False)
        except Exception:
            logger.exception("Failed to send OTP email to %s", to_email)
            raise
