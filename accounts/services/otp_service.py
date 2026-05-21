"""Secure OTP generation, storage, verification, and resend."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.exceptions import (
    OtpError,
    OtpExpiredError,
    OtpMaxAttemptsError,
    OtpResendCooldownError,
)
from accounts.models import EmailOtp
from accounts.services.email_service import AuthEmailService
from accounts.services.email_validation import EmailValidationService

logger = logging.getLogger("accounts.otp")


class OtpPurpose:
    EMAIL_VERIFICATION = EmailOtp.Purpose.EMAIL_VERIFICATION
    LOGIN = EmailOtp.Purpose.LOGIN
    PASSWORD_RESET = EmailOtp.Purpose.PASSWORD_RESET
    CONTACT_VERIFY = EmailOtp.Purpose.CONTACT_VERIFY

    LABELS = {
        EMAIL_VERIFICATION: "email verification",
        LOGIN: "sign-in",
        PASSWORD_RESET: "password reset",
        CONTACT_VERIFY: "verification",
    }


class OtpService:
    @classmethod
    def _cfg(cls) -> dict:
        return getattr(settings, "OTP", {})

    @classmethod
    def expiry_seconds(cls) -> int:
        return int(cls._cfg().get("EXPIRY_SECONDS", 300))

    @classmethod
    def resend_cooldown_seconds(cls) -> int:
        return int(cls._cfg().get("RESEND_COOLDOWN_SECONDS", 60))

    @classmethod
    def max_attempts(cls) -> int:
        return int(cls._cfg().get("MAX_ATTEMPTS", 5))

    @classmethod
    def _pepper(cls) -> bytes:
        key = getattr(settings, "OTP_HASH_SECRET", None) or settings.SECRET_KEY
        return str(key).encode("utf-8")

    @classmethod
    def generate_code(cls) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    @classmethod
    def hash_code(cls, code: str) -> str:
        digest = hmac.new(cls._pepper(), code.strip().encode("utf-8"), hashlib.sha256)
        return digest.hexdigest()

    @classmethod
    def _invalidate_active(cls, email: str, purpose: str) -> None:
        EmailOtp.objects.filter(
            email=email,
            purpose=purpose,
            is_used=False,
            expires_at__gt=timezone.now(),
        ).update(is_used=True)

    @classmethod
    def _latest_active(cls, email: str, purpose: str) -> EmailOtp | None:
        return (
            EmailOtp.objects.filter(
                email=email,
                purpose=purpose,
                is_used=False,
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )

    @classmethod
    def _check_resend_cooldown(cls, email: str, purpose: str) -> None:
        latest = (
            EmailOtp.objects.filter(email=email, purpose=purpose)
            .order_by("-created_at")
            .first()
        )
        if not latest:
            return
        elapsed = (timezone.now() - latest.created_at).total_seconds()
        cooldown = cls.resend_cooldown_seconds()
        if elapsed < cooldown:
            wait = int(cooldown - elapsed)
            raise OtpResendCooldownError(
                f"Please wait {wait} seconds before requesting another code."
            )

    @classmethod
    @transaction.atomic
    def issue(
        cls,
        *,
        email: str,
        purpose: str,
        user=None,
        send_email: bool = True,
        invalidate_previous: bool = True,
        enforce_cooldown: bool = False,
    ) -> tuple[EmailOtp, str | None]:
        """
        Create OTP, optionally email it. Returns (record, plaintext_code for DEBUG only).
        """
        normalized = EmailValidationService.validate(email)
        if enforce_cooldown:
            cls._check_resend_cooldown(normalized, purpose)
        if invalidate_previous:
            cls._invalidate_active(normalized, purpose)

        code = cls.generate_code()
        expires_at = timezone.now() + timedelta(seconds=cls.expiry_seconds())
        record = EmailOtp.objects.create(
            email=normalized,
            purpose=purpose,
            code_hash=cls.hash_code(code),
            user=user,
            expires_at=expires_at,
            max_attempts=cls.max_attempts(),
        )

        if send_email:
            label = OtpPurpose.LABELS.get(purpose, "verification")
            AuthEmailService.send_otp_email(
                to_email=normalized,
                code=code,
                purpose_label=label,
                expires_minutes=max(1, cls.expiry_seconds() // 60),
            )
            logger.info("OTP issued purpose=%s email=%s", purpose, normalized)

        preview = os.environ.get("OTP_DEV_PREVIEW", "").lower() in ("1", "true", "yes")
        if send_email:
            dev_code = code if (settings.DEBUG or preview) else None
        else:
            dev_code = code  # caller sends email in background
        return record, dev_code

    @classmethod
    def resend(
        cls,
        *,
        email: str,
        purpose: str,
        user=None,
        send_email: bool = True,
    ) -> tuple[EmailOtp, str | None]:
        """Invalidate previous OTPs and issue a new one (respects resend cooldown)."""
        return cls.issue(
            email=email,
            purpose=purpose,
            user=user,
            send_email=send_email,
            invalidate_previous=True,
            enforce_cooldown=True,
        )

    @classmethod
    def verify(cls, *, email: str, purpose: str, code: str) -> EmailOtp:
        normalized = EmailValidationService.normalize(email)
        if not code or not code.strip().isdigit():
            raise OtpError("Enter a valid 6-digit code.")

        record = cls._latest_active(normalized, purpose)
        if not record:
            raise OtpExpiredError()

        if record.attempts >= record.max_attempts:
            record.is_used = True
            record.save(update_fields=["is_used"])
            raise OtpMaxAttemptsError()

        if record.code_hash != cls.hash_code(code.strip()):
            record.attempts += 1
            record.save(update_fields=["attempts"])
            remaining = max(0, record.max_attempts - record.attempts)
            if record.attempts >= record.max_attempts:
                record.is_used = True
                record.save(update_fields=["is_used"])
                raise OtpMaxAttemptsError()
            raise OtpError(f"Invalid code. {remaining} attempt(s) remaining.")

        record.is_used = True
        record.save(update_fields=["is_used"])
        return record
