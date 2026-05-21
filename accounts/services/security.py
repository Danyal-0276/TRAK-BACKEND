"""Brute-force protection and suspicious-activity logging."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache

from accounts.exceptions import BruteForceLockoutError

logger = logging.getLogger("accounts.security")


def _cfg() -> dict:
    return getattr(settings, "AUTH_SECURITY", {})


class AuthSecurityService:
    """IP + identifier lockouts for login and OTP verification."""

    @classmethod
    def _login_fail_key(cls, identifier: str, ip: str) -> str:
        ident = (identifier or "").strip().lower()
        return f"auth:login_fail:{ident}:{ip or 'unknown'}"

    @classmethod
    def _otp_fail_key(cls, email: str, purpose: str, ip: str) -> str:
        return f"auth:otp_fail:{purpose}:{email.strip().lower()}:{ip or 'unknown'}"

    @classmethod
    def client_ip(cls, request) -> str:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "") or "unknown"

    @classmethod
    def check_login_allowed(cls, email: str, ip: str) -> None:
        cfg = _cfg()
        max_attempts = int(cfg.get("LOGIN_MAX_ATTEMPTS", 10))
        lockout_seconds = int(cfg.get("LOGIN_LOCKOUT_SECONDS", 900))
        key = cls._login_fail_key(email, ip)
        failures = int(cache.get(key) or 0)
        if failures >= max_attempts:
            logger.warning(
                "Login lockout active email=%s ip=%s failures=%s",
                email,
                ip,
                failures,
            )
            raise BruteForceLockoutError(
                f"Too many failed login attempts. Try again in {lockout_seconds // 60} minutes."
            )

    @classmethod
    def record_login_failure(cls, email: str, ip: str) -> None:
        cfg = _cfg()
        lockout_seconds = int(cfg.get("LOGIN_LOCKOUT_SECONDS", 900))
        key = cls._login_fail_key(email, ip)
        failures = int(cache.get(key) or 0) + 1
        cache.set(key, failures, timeout=lockout_seconds)
        if failures >= int(cfg.get("LOGIN_MAX_ATTEMPTS", 10)):
            logger.warning(
                "Suspicious login activity: lockout triggered email=%s ip=%s failures=%s",
                email,
                ip,
                failures,
            )
        else:
            logger.info(
                "Failed login attempt email=%s ip=%s count=%s",
                email,
                ip,
                failures,
            )

    @classmethod
    def clear_login_failures(cls, email: str, ip: str) -> None:
        cache.delete(cls._login_fail_key(email, ip))

    @classmethod
    def check_otp_verify_allowed(cls, email: str, purpose: str, ip: str) -> None:
        cfg = _cfg()
        max_attempts = int(cfg.get("OTP_VERIFY_MAX_ATTEMPTS", 5))
        lockout_seconds = int(cfg.get("OTP_VERIFY_LOCKOUT_SECONDS", 900))
        key = cls._otp_fail_key(email, purpose, ip)
        failures = int(cache.get(key) or 0)
        if failures >= max_attempts:
            logger.warning(
                "OTP verify lockout email=%s purpose=%s ip=%s failures=%s",
                email,
                purpose,
                ip,
                failures,
            )
            raise BruteForceLockoutError(
                "Too many incorrect verification attempts. Request a new code."
            )

    @classmethod
    def record_otp_verify_failure(cls, email: str, purpose: str, ip: str) -> None:
        cfg = _cfg()
        lockout_seconds = int(cfg.get("OTP_VERIFY_LOCKOUT_SECONDS", 900))
        key = cls._otp_fail_key(email, purpose, ip)
        failures = int(cache.get(key) or 0) + 1
        cache.set(key, failures, timeout=lockout_seconds)
        logger.info(
            "Failed OTP verify email=%s purpose=%s ip=%s count=%s",
            email,
            purpose,
            ip,
            failures,
        )

    @classmethod
    def clear_otp_verify_failures(cls, email: str, purpose: str, ip: str) -> None:
        cache.delete(cls._otp_fail_key(email, purpose, ip))

    @classmethod
    def log_suspicious(cls, event: str, **context) -> None:
        logger.warning("Suspicious auth event: %s | %s", event, context)
