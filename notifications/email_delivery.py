"""Send notification alert emails via Django SMTP (Gmail)."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_notification_email(to_email: str, *, subject: str, body: str, details: str = "") -> None:
    if not to_email or "@" not in to_email:
        return
    if not getattr(settings, "EMAIL_HOST", ""):
        logger.debug("EMAIL_HOST not configured; skip notification email")
        return

    lines = [body.strip()]
    if details:
        lines.append("")
        lines.append(details.strip())
    lines.append("")
    lines.append("— TRAK")
    message = "\n".join(lines)

    send_mail(
        subject=subject[:200] or "TRAK notification",
        message=message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[to_email],
        fail_silently=False,
    )
