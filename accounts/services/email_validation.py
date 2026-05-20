"""Advanced email validation: format, MX/DNS, disposable domains."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from email_validator import EmailNotValidError, validate_email

from accounts.exceptions import EmailValidationError

logger = logging.getLogger("accounts.security")

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@lru_cache(maxsize=1)
def _load_disposable_domains() -> frozenset[str]:
    path = _DATA_DIR / "disposable_domains.txt"
    domains: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                domains.add(line)
    cfg = getattr(settings, "EMAIL_VALIDATION", {})
    extra = cfg.get("DISPOSABLE_EXTRA", [])
    domains.update(d.lower().strip() for d in extra if d)
    return frozenset(domains)


class EmailValidationService:
    """Reusable email validation for serializers and public validate endpoint."""

    @classmethod
    def normalize(cls, email: str) -> str:
        return (email or "").strip().lower()

    @classmethod
    def validate(
        cls,
        email: str,
        *,
        check_mx: bool | None = None,
        block_disposable: bool | None = None,
    ) -> str:
        """
        Validate and return normalized email.
        Raises EmailValidationError with field='email' on failure.
        """
        normalized = cls.normalize(email)
        if not normalized:
            raise EmailValidationError("Email is required.")

        cfg = getattr(settings, "EMAIL_VALIDATION", {})
        if check_mx is None:
            check_mx = cfg.get("CHECK_MX", True)
        if block_disposable is None:
            block_disposable = cfg.get("BLOCK_DISPOSABLE", True)

        domain = normalized.split("@")[-1]
        skip_mx_domains = {d.lower().strip() for d in cfg.get("SKIP_MX_DOMAINS", ["admin.com"])}
        if domain in skip_mx_domains:
            check_mx = False

        try:
            result = validate_email(
                normalized,
                check_deliverability=bool(check_mx),
            )
            normalized = result.normalized.lower()
        except EmailNotValidError as exc:
            logger.info("Email format/MX rejected: %s (%s)", normalized, exc)
            raise EmailValidationError(str(exc)) from exc

        if block_disposable and domain in _load_disposable_domains():
            logger.warning("Disposable email rejected: %s", normalized)
            raise EmailValidationError(
                "Temporary or disposable email addresses are not allowed."
            )

        blocked_domains = cfg.get("BLOCKED_DOMAINS", [])
        if domain in {d.lower().strip() for d in blocked_domains}:
            raise EmailValidationError("This email domain is not allowed.")

        return normalized
