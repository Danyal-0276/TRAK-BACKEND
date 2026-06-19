"""Transactional email via Keplars, Resend API, or Django SMTP fallback."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail

logger = logging.getLogger("accounts.email")


class EmailDeliveryError(Exception):
    def __init__(self, message: str, *, code: str = "send_failed"):
        super().__init__(message)
        self.code = code


def keplars_configured() -> bool:
    return bool(getattr(settings, "KEPLARS_API_KEY", "").strip())


def resend_configured() -> bool:
    return bool(getattr(settings, "RESEND_API_KEY", "").strip())


def preferred_provider() -> str:
    forced = str(getattr(settings, "EMAIL_PROVIDER", "auto") or "auto").strip().lower()
    if forced in {"keplars", "resend", "smtp"}:
        return forced
    if keplars_configured():
        return "keplars"
    return "resend" if resend_configured() else "smtp"


def _send_via_keplars(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None,
    from_email: str,
) -> None:
    api_key = getattr(settings, "KEPLARS_API_KEY", "").strip()
    if not api_key:
        raise EmailDeliveryError("Keplars API key is not configured.", code="keplars_not_configured")

    base = str(getattr(settings, "KEPLARS_API_BASE", "https://api.keplars.com/api/v1") or "").rstrip("/")
    tier = str(getattr(settings, "KEPLARS_SEND_TIER", "instant") or "instant").strip().lower()
    if tier not in {"instant", "high", "async", "bulk"}:
        tier = "instant"

    body_content = html_body if html_body else text_body
    payload: dict = {
        "to": [to_email],
        "subject": subject,
        "body": body_content,
    }
    sender = (from_email or getattr(settings, "KEPLARS_FROM_EMAIL", "") or "").strip()
    if sender:
        payload["from"] = sender

    req = urllib.request.Request(
        f"{base}/send-email/{tier}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "TRAK-Backend",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=int(getattr(settings, "EMAIL_TIMEOUT", 20))) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status >= 400:
                raise EmailDeliveryError(f"Keplars returned HTTP {resp.status}", code="keplars_http")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict) and parsed.get("success") is False:
                code = str(parsed.get("code") or "keplars_rejected").lower()
                raise EmailDeliveryError(str(parsed.get("error") or "Keplars rejected the email."), code=code)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        logger.error("Keplars HTTP %s for %s: %s", exc.code, to_email, body)
        code = "keplars_http"
        lowered = body.lower()
        if exc.code == 403:
            code = "keplars_forbidden"
        if "domain" in lowered and ("verify" in lowered or "not verified" in lowered):
            code = "keplars_domain_not_verified"
        if exc.code == 429 or "rate_limit" in lowered:
            code = "keplars_rate_limited"
        raise EmailDeliveryError(f"Keplars failed ({exc.code}).", code=code) from exc
    except urllib.error.URLError as exc:
        logger.exception("Keplars network error for %s", to_email)
        raise EmailDeliveryError("Could not reach Keplars.", code="keplars_network") from exc


def _send_via_resend(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None,
    from_email: str,
) -> None:
    api_key = getattr(settings, "RESEND_API_KEY", "").strip()
    if not api_key:
        raise EmailDeliveryError("Resend API key is not configured.", code="resend_not_configured")

    payload: dict = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "TRAK-Backend",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=int(getattr(settings, "EMAIL_TIMEOUT", 20))) as resp:
            if resp.status >= 400:
                raise EmailDeliveryError(f"Resend returned HTTP {resp.status}", code="resend_http")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        logger.error("Resend HTTP %s for %s: %s", exc.code, to_email, body)
        code = "resend_http"
        if exc.code == 403 and "not verified" in body.lower():
            code = "resend_domain_not_verified"
        raise EmailDeliveryError(f"Resend failed ({exc.code}).", code=code) from exc
    except urllib.error.URLError as exc:
        logger.exception("Resend network error for %s", to_email)
        raise EmailDeliveryError("Could not reach Resend.", code="resend_network") from exc


def _send_via_smtp(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None,
    from_email: str,
) -> None:
    if html_body:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[to_email],
        )
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)
        return
    send_mail(
        subject,
        text_body,
        from_email,
        [to_email],
        fail_silently=False,
    )


def _map_smtp_error(exc: Exception) -> EmailDeliveryError:
    msg = str(exc).lower()
    if "daily user sending limit" in msg or "5.4.5" in msg:
        return EmailDeliveryError(
            "SMTP daily sending limit exceeded. Configure RESEND_API_KEY in .env.",
            code="smtp_daily_limit",
        )
    return EmailDeliveryError("SMTP delivery failed.", code="smtp_failed")


def send_transactional_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    from_email: str | None = None,
) -> str:
    """
    Send one email. Returns provider used: 'keplars', 'resend', or 'smtp'.
    In auto mode: Keplars → Resend → SMTP. Raises EmailDeliveryError on failure.
    """
    if not to_email or "@" not in to_email:
        raise EmailDeliveryError("Invalid recipient.", code="invalid_recipient")

    provider = preferred_provider()
    keplars_sender = (getattr(settings, "KEPLARS_FROM_EMAIL", "") or "").strip()
    resend_sender = (getattr(settings, "RESEND_FROM_EMAIL", "") or "TRAK <onboarding@resend.dev>").strip()
    smtp_sender = (from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "") or "TRAK <noreply@trak.local>").strip()
    last_error: EmailDeliveryError | None = None

    try_keplars = provider in {"auto", "keplars"} and keplars_configured()
    try_resend = provider in {"auto", "resend"} and resend_configured()
    try_smtp = provider in {"auto", "smtp"} and bool(getattr(settings, "EMAIL_HOST", ""))

    if try_keplars:
        try:
            _send_via_keplars(
                to_email=to_email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                from_email=keplars_sender or smtp_sender,
            )
            logger.info("Email sent via Keplars to %s", to_email)
            return "keplars"
        except EmailDeliveryError as exc:
            last_error = exc
            if provider == "keplars":
                raise
            logger.warning("Keplars failed for %s, trying next provider", to_email)

    if try_resend:
        try:
            _send_via_resend(
                to_email=to_email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                from_email=resend_sender,
            )
            logger.info("Email sent via Resend to %s", to_email)
            return "resend"
        except EmailDeliveryError as exc:
            last_error = exc
            if provider == "resend":
                raise
            logger.warning("Resend failed for %s, trying SMTP fallback", to_email)

    if try_smtp:
        try:
            _send_via_smtp(
                to_email=to_email,
                subject=subject,
                text_body=text_body,
                html_body=html_body,
                from_email=smtp_sender,
            )
            logger.info("Email sent via SMTP to %s", to_email)
            return "smtp"
        except Exception as exc:
            logger.exception("SMTP failed for %s", to_email)
            smtp_error = _map_smtp_error(exc)
            if last_error and provider == "auto":
                raise last_error from exc
            raise smtp_error from exc

    if last_error:
        raise last_error
    raise EmailDeliveryError(
        "No email provider configured. Set KEPLARS_API_KEY, RESEND_API_KEY, or SMTP settings in .env.",
        code="not_configured",
    )
