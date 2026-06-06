"""Send notification alert emails (Resend or SMTP)."""

from __future__ import annotations

import logging

from django.conf import settings

from accounts.email_transport import EmailDeliveryError, resend_configured, send_transactional_email

logger = logging.getLogger(__name__)


def send_notification_email(to_email: str, *, subject: str, body: str, details: str = "") -> None:
    if not to_email or "@" not in to_email:
        return
    if not resend_configured() and not getattr(settings, "EMAIL_HOST", ""):
        logger.debug("No email provider configured; skip notification email")
        return

    lines = [body.strip()]
    if details:
        lines.append("")
        lines.append(details.strip())
    lines.append("")
    lines.append("— TRAK")
    message = "\n".join(lines)

    try:
        send_transactional_email(
            to_email=to_email,
            subject=subject[:200] or "TRAK notification",
            text_body=message,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        )
    except EmailDeliveryError:
        logger.exception("Notification email failed for %s", to_email)
        raise
