import logging
import os
import random
import re
import secrets
import urllib.parse
import urllib.request
import urllib.error
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.mail import send_mail
from django.http import JsonResponse
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.conf import settings
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .password_reset_utils import build_reset_url, send_password_reset_email
from .serializers import (
    CustomTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)
from news.mongo_db import get_db

User = get_user_model()
logger = logging.getLogger(__name__)
_profile_indexes_ready = False
_follow_indexes_ready = False


def _is_email(identity: str) -> bool:
    return bool(re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", identity or ""))


def _normalize_phone(phone: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", (phone or "").strip())
    return cleaned


def _otp_cache_key(channel: str, identity: str) -> str:
    return f"auth:otp:{channel}:{identity.lower()}"


def _social_state_cache_key(state: str) -> str:
    return f"auth:social:state:{state}"


def _social_ticket_cache_key(ticket: str) -> str:
    return f"auth:social:ticket:{ticket}"


def _profile_collection():
    global _profile_indexes_ready
    col = get_db()["user_profiles"]
    if not _profile_indexes_ready:
        col.create_index("user_id", unique=True)
        _profile_indexes_ready = True
    return col


def _follow_collection():
    global _follow_indexes_ready
    col = get_db()["user_follows"]
    if not _follow_indexes_ready:
        col.create_index([("follower_user_id", 1), ("followed_user_id", 1)], unique=True)
        col.create_index("follower_user_id")
        col.create_index("followed_user_id")
        _follow_indexes_ready = True
    return col


def _get_profile(user_id: int) -> dict:
    col = _profile_collection()
    row = col.find_one({"user_id": user_id})
    if row:
        return row
    default = {
        "user_id": user_id,
        "full_name": "",
        "username": "",
        "phone": "",
        "email_verified": False,
        "phone_verified": False,
        "bio": "",
        "avatar_image": "",
        "followers_count": 0,
        "following_count": 0,
    }
    col.insert_one(default)
    return default


def _user_payload(user: User) -> dict:
    p = _get_profile(user.pk)
    follows = _follow_collection()
    followers_count = follows.count_documents({"followed_user_id": user.pk})
    following_count = follows.count_documents({"follower_user_id": user.pk})
    username = (p.get("username") or "").strip()
    if not username:
        username = (user.email or "").split("@")[0]
    return {
        **UserSerializer(user).data,
        "full_name": p.get("full_name") or "",
        "username": username,
        "phone": p.get("phone") or "",
        "email_verified": bool(p.get("email_verified")),
        "phone_verified": bool(p.get("phone_verified")),
        "bio": p.get("bio") or "",
        "avatar_image": p.get("avatar_image") or "",
        "followers_count": int(followers_count),
        "following_count": int(following_count),
    }


def _api_json(method: str, url: str, *, data: dict | None = None, headers: dict | None = None) -> dict:
    payload = None
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if data is not None:
        payload = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url=url, data=payload, method=method, headers=request_headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _exchange_google_code(code: str) -> str:
    token = _api_json(
        "POST",
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": getattr(settings, "GOOGLE_CLIENT_ID", ""),
            "client_secret": getattr(settings, "GOOGLE_CLIENT_SECRET", ""),
            "redirect_uri": getattr(settings, "GOOGLE_REDIRECT_URI", ""),
            "grant_type": "authorization_code",
        },
    )
    id_token = token.get("id_token")
    if not id_token:
        raise ValueError("Google token exchange failed")
    info = _api_json("GET", f"https://oauth2.googleapis.com/tokeninfo?id_token={urllib.parse.quote(id_token)}")
    email = (info.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Google account did not return an email")
    return email


def _exchange_github_code(code: str) -> str:
    token = _api_json(
        "POST",
        "https://github.com/login/oauth/access_token",
        data={
            "client_id": getattr(settings, "GITHUB_CLIENT_ID", ""),
            "client_secret": getattr(settings, "GITHUB_CLIENT_SECRET", ""),
            "code": code,
            "redirect_uri": getattr(settings, "GITHUB_REDIRECT_URI", ""),
        },
        headers={"Accept": "application/json"},
    )
    access_token = token.get("access_token")
    if not access_token:
        raise ValueError("GitHub token exchange failed")
    emails_req = urllib.request.Request(
        "https://api.github.com/user/emails",
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "TRAK-Auth/1.0",
        },
    )
    with urllib.request.urlopen(emails_req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8") or "[]")
    if isinstance(data, list):
        for item in data:
            if item.get("primary") and item.get("verified") and item.get("email"):
                return str(item["email"]).strip().lower()
        for item in data:
            if item.get("email"):
                return str(item["email"]).strip().lower()
    raise ValueError("GitHub account did not return an email")


def _build_social_auth_url(provider: str, state: str) -> str:
    if provider == "google":
        query = urllib.parse.urlencode(
            {
                "client_id": getattr(settings, "GOOGLE_CLIENT_ID", ""),
                "redirect_uri": getattr(settings, "GOOGLE_REDIRECT_URI", ""),
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "prompt": "select_account",
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
    if provider == "github":
        query = urllib.parse.urlencode(
            {
                "client_id": getattr(settings, "GITHUB_CLIENT_ID", ""),
                "redirect_uri": getattr(settings, "GITHUB_REDIRECT_URI", ""),
                "scope": "read:user user:email",
                "state": state,
            }
        )
        return f"https://github.com/login/oauth/authorize?{query}"
    raise ValueError("Unsupported provider")


class OtpRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        identity = str(request.data.get("identity") or "").strip()
        if not identity:
            return Response({"detail": "identity is required"}, status=status.HTTP_400_BAD_REQUEST)

        channel = "email" if _is_email(identity) else "phone"
        normalized_identity = identity.lower() if channel == "email" else _normalize_phone(identity)
        if channel == "phone" and not normalized_identity:
            return Response({"detail": "Invalid phone number"}, status=status.HTTP_400_BAD_REQUEST)

        otp = f"{random.randint(0, 999999):06d}"
        cache.set(_otp_cache_key(channel, normalized_identity), otp, timeout=600)

        if channel == "email":
            try:
                send_mail(
                    subject="Your TRAK verification code",
                    message=f"Your TRAK verification code is: {otp}\nIt expires in 10 minutes.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[normalized_identity],
                    fail_silently=False,
                )
            except Exception:
                logger.exception("Failed sending OTP email to %s", normalized_identity)
        else:
            # Free-mode default: expose code in dev response and log on server.
            # (No paid SMS provider required.)
            logger.info("TRAK OTP for phone %s is %s", normalized_identity, otp)

        return Response(
            {
                "detail": f"Verification code sent to your {channel}.",
                "channel": channel,
                "dev_code": otp if settings.DEBUG else None,
            },
            status=status.HTTP_200_OK,
        )


class OtpVerifyView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        identity = str(request.data.get("identity") or "").strip()
        code = str(request.data.get("code") or "").strip()
        if not identity or not code:
            return Response({"detail": "identity and code are required"}, status=status.HTTP_400_BAD_REQUEST)

        channel = "email" if _is_email(identity) else "phone"
        normalized_identity = identity.lower() if channel == "email" else _normalize_phone(identity)
        cache_key = _otp_cache_key(channel, normalized_identity)
        expected = cache.get(cache_key)
        if not expected or expected != code:
            return Response({"detail": "Invalid or expired verification code."}, status=status.HTTP_400_BAD_REQUEST)

        cache.delete(cache_key)
        if channel == "email":
            email = normalized_identity
            user = User.objects.filter(email=email).first()
            if not user:
                user = User.objects.create_user(email=email, password=User.objects.make_random_password())
        else:
            # Login should bind to an existing profile using this phone number.
            profile_row = _profile_collection().find_one({"phone": normalized_identity})
            if not profile_row:
                return Response({"detail": "No account is linked to this phone number."}, status=status.HTTP_400_BAD_REQUEST)
            try:
                linked_user_id = int(profile_row.get("user_id"))
            except (TypeError, ValueError):
                return Response({"detail": "Invalid phone profile mapping."}, status=status.HTTP_400_BAD_REQUEST)
            user = User.objects.filter(pk=linked_user_id).first()
            if not user:
                return Response({"detail": "Account linked to this phone no longer exists."}, status=status.HTTP_400_BAD_REQUEST)
        profile = _get_profile(user.pk)
        if channel == "phone":
            _profile_collection().update_one(
                {"user_id": user.pk},
                {"$set": {"phone": normalized_identity, "phone_verified": True}},
                upsert=True,
            )
        else:
            _profile_collection().update_one(
                {"user_id": user.pk},
                {"$set": {"email_verified": True}},
                upsert=True,
            )

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": _user_payload(user),
            },
            status=status.HTTP_200_OK,
        )


class SocialProvidersView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        providers = [
            {"id": "google", "name": "Google", "enabled": bool(getattr(settings, "GOOGLE_CLIENT_ID", ""))},
            {"id": "github", "name": "GitHub", "enabled": bool(getattr(settings, "GITHUB_CLIENT_ID", ""))},
            {"id": "twitter", "name": "Twitter/X", "enabled": False},
        ]
        return Response({"providers": providers})


class SocialStartView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, provider: str):
        provider = provider.strip().lower()
        if provider not in {"google", "github"}:
            return Response({"detail": "Unsupported provider"}, status=status.HTTP_400_BAD_REQUEST)
        state = secrets.token_urlsafe(24)
        cache.set(_social_state_cache_key(state), provider, timeout=600)
        try:
            url = _build_social_auth_url(provider, state)
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        from django.shortcuts import redirect

        return redirect(url)


class SocialCallbackView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, provider: str):
        provider = provider.strip().lower()
        state = str(request.query_params.get("state") or "").strip()
        code = str(request.query_params.get("code") or "").strip()
        if provider not in {"google", "github"}:
            return Response({"detail": "Unsupported provider"}, status=status.HTTP_400_BAD_REQUEST)
        if not state or cache.get(_social_state_cache_key(state)) != provider:
            return Response({"detail": "Invalid social state"}, status=status.HTTP_400_BAD_REQUEST)
        cache.delete(_social_state_cache_key(state))
        if not code:
            return Response({"detail": "Missing social auth code"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            email = _exchange_google_code(code) if provider == "google" else _exchange_github_code(code)
        except Exception as exc:
            logger.exception("Social callback failed for %s", provider)
            return Response({"detail": f"Social login failed: {exc}"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email).first()
        if not user:
            user = User.objects.create_user(email=email, password=User.objects.make_random_password())

        refresh = RefreshToken.for_user(user)
        ticket = secrets.token_urlsafe(32)
        cache.set(
            _social_ticket_cache_key(ticket),
            {"refresh": str(refresh), "access": str(refresh.access_token), "user": UserSerializer(user).data},
            timeout=120,
        )
        frontend_url = getattr(settings, "SOCIAL_AUTH_FRONTEND_URL", "http://127.0.0.1:5173/login")
        from django.shortcuts import redirect

        return redirect(f"{frontend_url}?social_ticket={urllib.parse.quote(ticket)}")


class SocialCompleteView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ticket = str(request.data.get("ticket") or "").strip()
        if not ticket:
            return Response({"detail": "ticket is required"}, status=status.HTTP_400_BAD_REQUEST)
        payload = cache.get(_social_ticket_cache_key(ticket))
        if not payload:
            return Response({"detail": "Invalid or expired ticket"}, status=status.HTTP_400_BAD_REQUEST)
        cache.delete(_social_ticket_cache_key(ticket))
        return Response(payload, status=status.HTTP_200_OK)


class SocialDemoLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        enabled = getattr(settings, "ALLOW_DEMO_SOCIAL_LOGIN", False)
        if not settings.DEBUG or not enabled:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        provider = str(request.data.get("provider") or "").strip().lower()
        email = str(request.data.get("email") or "").strip().lower()
        if provider not in {"google", "github", "twitter"}:
            return Response({"detail": "Unsupported social provider"}, status=status.HTTP_400_BAD_REQUEST)
        if not email or not _is_email(email):
            return Response({"detail": "A valid email is required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email).first()
        if not user:
            user = User.objects.create_user(email=email, password=User.objects.make_random_password())
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": _user_payload(user),
                "provider": provider,
            },
            status=status.HTTP_200_OK,
        )


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        full_name = str(request.data.get("full_name") or "").strip()
        phone = _normalize_phone(str(request.data.get("phone") or ""))
        email_prefix = (user.email or "").split("@")[0]
        _profile_collection().update_one(
            {"user_id": user.pk},
            {
                "$set": {
                    "full_name": full_name,
                    "username": email_prefix,
                    "phone": phone,
                    "email_verified": False,
                    "phone_verified": False,
                    "bio": "",
                    "avatar_image": "",
                    "followers_count": 0,
                    "following_count": 0,
                }
            },
            upsert=True,
        )
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": _user_payload(user),
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


class MeView(APIView):
    def get(self, request):
        return Response(_user_payload(request.user), status=status.HTTP_200_OK)


class ProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(_user_payload(request.user), status=status.HTTP_200_OK)

    def patch(self, request):
        allowed = {"full_name", "username", "phone", "bio", "avatar_image"}
        payload = {}
        for key in allowed:
            if key in request.data:
                val = str(request.data.get(key) or "").strip()
                if key == "phone":
                    val = _normalize_phone(val)
                if key == "username":
                    if len(val) < 3:
                        return Response({"detail": "username must be at least 3 characters."}, status=status.HTTP_400_BAD_REQUEST)
                    if not re.match(r"^[A-Za-z0-9_]+$", val):
                        return Response({"detail": "username may contain only letters, numbers, and underscores."}, status=status.HTTP_400_BAD_REQUEST)
                if key == "avatar_image":
                    if val and not (val.startswith("data:image/") or val.startswith("http://") or val.startswith("https://")):
                        return Response({"detail": "avatar_image must be an image data URL or image URL."}, status=status.HTTP_400_BAD_REQUEST)
                payload[key] = val
        if not payload:
            return Response({"detail": "No updatable fields provided."}, status=status.HTTP_400_BAD_REQUEST)
        _profile_collection().update_one({"user_id": request.user.pk}, {"$set": payload}, upsert=True)
        return Response(_user_payload(request.user), status=status.HTTP_200_OK)


class VerifyContactRequestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        channel = str(request.data.get("channel") or "").strip().lower()
        if channel not in {"email", "phone"}:
            return Response({"detail": "channel must be email or phone"}, status=status.HTTP_400_BAD_REQUEST)
        profile = _get_profile(request.user.pk)
        if channel == "email":
            identity = request.user.email
        else:
            identity = _normalize_phone(str(request.data.get("phone") or profile.get("phone") or ""))
            if not identity:
                return Response({"detail": "Phone is required to verify phone."}, status=status.HTTP_400_BAD_REQUEST)
            _profile_collection().update_one({"user_id": request.user.pk}, {"$set": {"phone": identity}}, upsert=True)

        otp = f"{random.randint(0, 999999):06d}"
        cache.set(_otp_cache_key(channel, identity), otp, timeout=600)
        if channel == "email":
            send_mail(
                subject="Your TRAK verification code",
                message=f"Your TRAK verification code is: {otp}\nIt expires in 10 minutes.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[identity],
                fail_silently=True,
            )
        else:
            logger.info("TRAK verify OTP for phone %s is %s", identity, otp)
        return Response(
            {"detail": "Verification code sent.", "channel": channel, "dev_code": otp if settings.DEBUG else None},
            status=status.HTTP_200_OK,
        )


class VerifyContactConfirmView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        channel = str(request.data.get("channel") or "").strip().lower()
        code = str(request.data.get("code") or "").strip()
        if channel not in {"email", "phone"}:
            return Response({"detail": "channel must be email or phone"}, status=status.HTTP_400_BAD_REQUEST)
        if not code:
            return Response({"detail": "code is required"}, status=status.HTTP_400_BAD_REQUEST)
        profile = _get_profile(request.user.pk)
        identity = request.user.email if channel == "email" else _normalize_phone(str(profile.get("phone") or ""))
        if not identity:
            return Response({"detail": "No phone available for verification."}, status=status.HTTP_400_BAD_REQUEST)
        expected = cache.get(_otp_cache_key(channel, identity))
        if not expected or expected != code:
            return Response({"detail": "Invalid or expired verification code."}, status=status.HTTP_400_BAD_REQUEST)
        cache.delete(_otp_cache_key(channel, identity))
        field = "email_verified" if channel == "email" else "phone_verified"
        _profile_collection().update_one({"user_id": request.user.pk}, {"$set": {field: True}}, upsert=True)
        return Response(_user_payload(request.user), status=status.HTTP_200_OK)


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
        reset_preview = None
        if user is not None and user.is_active:
            try:
                if settings.DEBUG:
                    reset_url, uid, token = build_reset_url(user)
                    reset_preview = {"reset_url": reset_url, "uid": uid, "token": token}
                send_password_reset_email(user)
            except Exception:
                logger.exception("Password reset email failed for %s", email)
        payload = {"detail": "If an account exists for this address, password reset instructions were sent."}
        if reset_preview:
            payload["debug_reset_preview"] = reset_preview
        return Response(payload, status=status.HTTP_200_OK)


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


class FollowView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        target_user_id = request.data.get("user_id")
        try:
            target_user_id = int(target_user_id)
        except (TypeError, ValueError):
            return Response({"detail": "user_id must be a valid integer."}, status=status.HTTP_400_BAD_REQUEST)
        if target_user_id == request.user.pk:
            return Response({"detail": "You cannot follow yourself."}, status=status.HTTP_400_BAD_REQUEST)
        target_user = User.objects.filter(pk=target_user_id).first()
        if not target_user:
            return Response({"detail": "Target user not found."}, status=status.HTTP_404_NOT_FOUND)
        _follow_collection().update_one(
            {"follower_user_id": request.user.pk, "followed_user_id": target_user_id},
            {"$set": {"follower_user_id": request.user.pk, "followed_user_id": target_user_id}},
            upsert=True,
        )
        return Response(
            {
                "detail": "Followed successfully.",
                "me": _user_payload(request.user),
                "target": _user_payload(target_user),
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request):
        target_user_id = request.data.get("user_id") or request.query_params.get("user_id")
        try:
            target_user_id = int(target_user_id)
        except (TypeError, ValueError):
            return Response({"detail": "user_id must be a valid integer."}, status=status.HTTP_400_BAD_REQUEST)
        _follow_collection().delete_one({"follower_user_id": request.user.pk, "followed_user_id": target_user_id})
        target_user = User.objects.filter(pk=target_user_id).first()
        return Response(
            {
                "detail": "Unfollowed successfully.",
                "me": _user_payload(request.user),
                "target": _user_payload(target_user) if target_user else None,
            },
            status=status.HTTP_200_OK,
        )


def health(request):
    return JsonResponse({"status": "ok", "service": "accounts"})
