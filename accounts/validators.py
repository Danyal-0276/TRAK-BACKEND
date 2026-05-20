"""DRF / Django validators wrapping auth services."""

from __future__ import annotations

from accounts.exceptions import EmailValidationError
from accounts.services.email_validation import EmailValidationService


def validate_email_address(value: str) -> str:
    try:
        return EmailValidationService.validate(value)
    except EmailValidationError as exc:
        from rest_framework import serializers

        raise serializers.ValidationError(exc.detail) from exc
