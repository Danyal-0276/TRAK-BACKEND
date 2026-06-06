"""Transactional auth emails with HTML templates."""

from __future__ import annotations

import logging

from django.conf import settings
from django.template.loader import render_to_string

from accounts.email_transport import EmailDeliveryError, send_transactional_email

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
        try:
            send_transactional_email(
                to_email=to_email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
            )
        except EmailDeliveryError:
            raise
        except Exception:
            logger.exception("Failed to send OTP email to %s", to_email)
            raise
