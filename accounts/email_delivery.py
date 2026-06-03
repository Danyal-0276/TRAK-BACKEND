"""Background OTP email delivery (survives after HTTP response on Render)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import close_old_connections

from accounts.services.email_service import AuthEmailService

logger = logging.getLogger("accounts.email")

_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(getattr(settings, "EMAIL_WORKER_THREADS", 4)),
    thread_name_prefix="trak-email",
)


def _send_otp_email_task(
    *,
    to_email: str,
    code: str,
    purpose_label: str,
    expires_minutes: int,
) -> None:
    close_old_connections()
    try:
        AuthEmailService.send_otp_email(
            to_email=to_email,
            code=code,
            purpose_label=purpose_label,
            expires_minutes=expires_minutes,
        )
        logger.info("OTP email sent to %s (%s)", to_email, purpose_label)
    except Exception:
        logger.exception("OTP email failed for %s (%s)", to_email, purpose_label)
    finally:
        close_old_connections()


def queue_otp_email(
    *,
    to_email: str,
    code: str,
    purpose_label: str,
    expires_minutes: int,
) -> None:
    """Queue OTP email on a persistent worker thread (non-blocking for the API)."""
    _EXECUTOR.submit(
        _send_otp_email_task,
        to_email=to_email,
        code=code,
        purpose_label=purpose_label,
        expires_minutes=expires_minutes,
    )
