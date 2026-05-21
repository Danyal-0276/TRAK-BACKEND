"""Auth domain exceptions mapped to DRF responses in views."""

from __future__ import annotations


class AuthServiceError(Exception):
    """Base auth service error with optional field mapping for validation responses."""

    default_code = "auth_error"
    default_detail = "Authentication error."
    status_code = 400

    def __init__(self, detail: str | None = None, *, field: str | None = None):
        self.detail = detail or self.default_detail
        self.field = field
        super().__init__(self.detail)

    def as_validation_dict(self) -> dict:
        if self.field:
            return {self.field: [self.detail]}
        return {"detail": self.detail}


class EmailValidationError(AuthServiceError):
    default_code = "invalid_email"
    default_detail = "Invalid email address."
    field = "email"


class OtpError(AuthServiceError):
    default_code = "otp_error"
    default_detail = "Invalid or expired verification code."


class OtpExpiredError(OtpError):
    default_detail = "Verification code has expired. Request a new code."


class OtpMaxAttemptsError(OtpError):
    status_code = 429
    default_detail = "Too many incorrect attempts. Request a new code."


class OtpResendCooldownError(AuthServiceError):
    default_code = "resend_cooldown"
    default_detail = "Please wait before requesting another code."
    status_code = 429
    field = "email"


class BruteForceLockoutError(AuthServiceError):
    default_code = "account_locked"
    default_detail = "Too many failed attempts. Try again later."
    status_code = 429
