"""Email validation and OTP email-verification API views."""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.decorators import RatelimitedAPIMixin
from accounts.exceptions import (
    AuthServiceError,
    BruteForceLockoutError,
    EmailValidationError,
    OtpError,
    OtpResendCooldownError,
)
from accounts.serializers import (
    EmailValidateSerializer,
    EmailVerificationResendSerializer,
    EmailVerificationSendSerializer,
    EmailVerificationVerifySerializer,
)
from accounts.services.email_validation import EmailValidationService
from accounts.services.otp_service import OtpPurpose, OtpService
from accounts.services.security import AuthSecurityService
from accounts.views import _mark_email_verified, _user_payload

User = get_user_model()
logger = logging.getLogger("accounts.security")


def _handle_service_error(exc: AuthServiceError) -> Response:
    if exc.field and hasattr(exc, "as_validation_dict"):
        return Response(exc.as_validation_dict(), status=exc.status_code)
    return Response({"detail": exc.detail}, status=exc.status_code)


class EmailValidateView(RatelimitedAPIMixin, APIView):
    """POST { email } — public format/MX/disposable validation."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "email_validate"
    ratelimit_rate = "30/m"

    def post(self, request):
        ser = EmailValidateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data["email"]
        try:
            normalized = EmailValidationService.validate(email)
        except EmailValidationError as exc:
            return Response(exc.as_validation_dict(), status=status.HTTP_400_BAD_REQUEST)
        return Response({"email": normalized, "valid": True}, status=status.HTTP_200_OK)


class EmailVerificationSendView(RatelimitedAPIMixin, APIView):
    """POST — send verification OTP to the authenticated user's email."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_send"
    ratelimit_rate = "10/m"

    def post(self, request):
        ser = EmailVerificationSendSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = request.user
        if user.email_verified:
            return Response(
                {"detail": "Email is already verified.", "user": _user_payload(user)},
                status=status.HTTP_200_OK,
            )
        ip = AuthSecurityService.client_ip(request)
        try:
            _, dev_code = OtpService.issue(
                email=user.email,
                purpose=OtpPurpose.EMAIL_VERIFICATION,
                user=user,
                send_email=True,
                invalidate_previous=True,
                enforce_cooldown=False,
            )
        except AuthServiceError as exc:
            return _handle_service_error(exc)
        payload = {"detail": "Verification code sent to your email."}
        if settings.DEBUG and dev_code:
            payload["dev_code"] = dev_code
        return Response(payload, status=status.HTTP_200_OK)


class EmailVerificationVerifyView(RatelimitedAPIMixin, APIView):
    """POST { code } — verify OTP and set email_verified=True."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_verify"
    ratelimit_rate = "20/m"

    def post(self, request):
        ser = EmailVerificationVerifySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        code = ser.validated_data["code"]
        user = request.user
        ip = AuthSecurityService.client_ip(request)
        try:
            AuthSecurityService.check_otp_verify_allowed(
                user.email, OtpPurpose.EMAIL_VERIFICATION, ip
            )
            OtpService.verify(
                email=user.email,
                purpose=OtpPurpose.EMAIL_VERIFICATION,
                code=code,
            )
            AuthSecurityService.clear_otp_verify_failures(
                user.email, OtpPurpose.EMAIL_VERIFICATION, ip
            )
            _mark_email_verified(user)
            user.refresh_from_db()
            return Response(
                {"detail": "Email verified successfully.", "user": _user_payload(user)},
                status=status.HTTP_200_OK,
            )
        except BruteForceLockoutError as exc:
            return _handle_service_error(exc)
        except OtpError as exc:
            AuthSecurityService.record_otp_verify_failure(
                user.email, OtpPurpose.EMAIL_VERIFICATION, ip
            )
            return Response({"code": [exc.detail]}, status=exc.status_code)
        except AuthServiceError as exc:
            AuthSecurityService.record_otp_verify_failure(
                user.email, OtpPurpose.EMAIL_VERIFICATION, ip
            )
            return _handle_service_error(exc)


class EmailVerificationResendView(RatelimitedAPIMixin, APIView):
    """POST — resend verification OTP (invalidates previous, enforces cooldown)."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_send"
    ratelimit_rate = "10/m"

    def post(self, request):
        ser = EmailVerificationResendSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = request.user
        if user.email_verified:
            return Response(
                {"detail": "Email is already verified."},
                status=status.HTTP_200_OK,
            )
        try:
            _, dev_code = OtpService.resend(
                email=user.email,
                purpose=OtpPurpose.EMAIL_VERIFICATION,
                user=user,
            )
        except OtpResendCooldownError as exc:
            return _handle_service_error(exc)
        except AuthServiceError as exc:
            return _handle_service_error(exc)
        payload = {"detail": "A new verification code was sent to your email."}
        if settings.DEBUG and dev_code:
            payload["dev_code"] = dev_code
        return Response(payload, status=status.HTTP_200_OK)
