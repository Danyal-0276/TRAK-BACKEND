import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.http import JsonResponse
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .password_reset_utils import send_password_reset_email
from .serializers import (
    CustomTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)

User = get_user_model()
logger = logging.getLogger(__name__)


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(TokenObtainPairView):
    permission_classes = [permissions.AllowAny]
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class ThrottledTokenRefreshView(TokenRefreshView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "refresh"


class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class PasswordResetRequestView(APIView):
    """POST { email } — always returns 200; sends email if user exists."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        ser = PasswordResetRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data["email"].strip().lower()
        user = User.objects.filter(email__iexact=email).first()
        if user is not None and user.is_active:
            try:
                send_password_reset_email(user)
            except Exception:
                logger.exception("Password reset email failed for %s", email)
        return Response(
            {"detail": "If an account exists for this address, password reset instructions were sent."},
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    """POST { uid, token, password, password_confirm } — Django token validation."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        ser = PasswordResetConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        uid_b64 = ser.validated_data["uid"]
        token = ser.validated_data["token"]
        try:
            uid = force_str(urlsafe_base64_decode(uid_b64))
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, OverflowError, TypeError):
            return Response(
                {"detail": "Invalid or expired reset link."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "Invalid or expired reset link."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(ser.validated_data["password"])
        user.save()
        return Response(
            {"detail": "Password has been reset. You can sign in with your new password."},
            status=status.HTTP_200_OK,
        )


def health(request):
    return JsonResponse({"status": "ok", "service": "accounts"})
