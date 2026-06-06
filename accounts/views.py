import base64
import logging
import os
import random
import re
import secrets
import time
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
from django.utils import timezone
from django.conf import settings
from rest_framework import generics, permissions, status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .decorators import RatelimitedAPIMixin
from .email_delivery import queue_otp_email, send_otp_email_sync
from .exceptions import AuthServiceError, BruteForceLockoutError, OtpError
from .password_reset_utils import (
    consume_password_reset_session,
    issue_password_reset_session,
)
from .serializers import (
    CustomTokenObtainPairSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetOtpConfirmSerializer,
    PasswordResetOtpVerifySerializer,
    PasswordResetRequestSerializer,
    RegisterSerializer,
    UserSerializer,
)
from .services.email_validation import EmailValidationService
from .services.otp_service import OtpPurpose, OtpService
from .services.security import AuthSecurityService
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


def _pwreset_otp_key(email: str) -> str:
    return f"auth:pwreset_otp:{(email or '').strip().lower()}"


def _social_state_cache_key(state: str) -> str:
    return f"auth:social:state:{state}"


def _social_ticket_cache_key(ticket: str) -> str:
    return f"auth:social:ticket:{ticket}"


def _random_user_password() -> str:
    """Placeholder password for OAuth/Firebase users (they sign in via provider, not password)."""
    return secrets.token_urlsafe(32)


def _profile_collection():
    global _profile_indexes_ready
    col = get_db()["user_profiles"]
    if not _profile_indexes_ready:
        try:
            col.create_index("user_id", unique=True)
            _profile_indexes_ready = True
        except Exception:
            logger.exception("Failed to initialize user_profiles indexes")
    return col


def _follow_collection():
    global _follow_indexes_ready
    col = get_db()["user_follows"]
    if not _follow_indexes_ready:
        try:
            col.create_index([("follower_user_id", 1), ("followed_user_id", 1)], unique=True)
            col.create_index("follower_user_id")
            col.create_index("followed_user_id")
            _follow_indexes_ready = True
        except Exception:
            logger.exception("Failed to initialize user_follows indexes")
    return col


def _get_profile(user_id: int) -> dict:
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
    try:
        col = _profile_collection()
        row = col.find_one({"user_id": user_id})
        if row:
            return row
        col.insert_one(default)
    except Exception:
        logger.exception("Failed to read/create profile for user_id=%s", user_id)
    return default


def _mark_email_verified(user: User) -> None:
    if not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])
    try:
        _profile_collection().update_one(
            {"user_id": user.pk},
            {"$set": {"email_verified": True}},
            upsert=True,
        )
    except Exception:
        logger.exception("Mongo email_verified sync failed for user_id=%s", user.pk)


def _onboarding_complete(user_id: int) -> bool:
    """True when the user has saved feed keywords (finished tag/keyword onboarding)."""
    try:
        from news.mongo_db import user_keywords_collection

        row = user_keywords_collection().find_one({"user_id": user_id}) or {}
        keywords = row.get("keywords") or []
        return any(str(k).strip() for k in keywords)
    except Exception:
        logger.exception("Failed to read onboarding keywords for user_id=%s", user_id)
        return False


def _user_payload(user: User) -> dict:
    p = _get_profile(user.pk)
    followers_count = 0
    following_count = 0
    try:
        follows = _follow_collection()
        followers_count = follows.count_documents({"followed_user_id": user.pk})
        following_count = follows.count_documents({"follower_user_id": user.pk})
    except Exception:
        logger.exception("Failed to calculate follow counts for user_id=%s", user.pk)
    username = (p.get("username") or "").strip()
    if not username:
        username = (user.email or "").split("@")[0]
    return {
        **UserSerializer(user).data,
        "email_verified": bool(getattr(user, "email_verified", False) or p.get("email_verified")),
        "full_name": p.get("full_name") or "",
        "username": username,
        "phone": p.get("phone") or "",
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


def _exchange_facebook_code(code: str) -> str:
    params = {
        "client_id": getattr(settings, "FACEBOOK_CLIENT_ID", ""),
        "redirect_uri": getattr(settings, "FACEBOOK_REDIRECT_URI", ""),
        "client_secret": getattr(settings, "FACEBOOK_CLIENT_SECRET", ""),
        "code": code,
    }
    url = "https://graph.facebook.com/v21.0/oauth/access_token?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=25) as resp:
            token_payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Facebook token exchange failed: {body}") from exc
    access_token = token_payload.get("access_token")
    if not access_token:
        raise ValueError("Facebook token exchange failed")
    me_url = "https://graph.facebook.com/me?fields=id,email&access_token=" + urllib.parse.quote(access_token)
    with urllib.request.urlopen(me_url, timeout=25) as resp:
        me = json.loads(resp.read().decode("utf-8") or "{}")
    email = me.get("email")
    if not email:
        raise ValueError("Facebook did not return email — add email permission in Meta app settings.")
    return str(email).strip().lower()


def _apple_client_secret_jwt() -> str:
    import jwt

    team_id = getattr(settings, "APPLE_TEAM_ID", "").strip()
    key_id = getattr(settings, "APPLE_KEY_ID", "").strip()
    client_id = getattr(settings, "APPLE_CLIENT_ID", "").strip()
    raw_key = getattr(settings, "APPLE_PRIVATE_KEY", "").strip()
    if not (team_id and key_id and client_id and raw_key):
        raise ValueError("Apple Sign-In is not configured (APPLE_TEAM_ID, APPLE_KEY_ID, APPLE_CLIENT_ID, APPLE_PRIVATE_KEY).")
    private_key = raw_key.replace("\\n", "\n")
    now = int(time.time())
    payload = {
        "iss": team_id,
        "iat": now,
        "exp": now + 3600 * 24 * 1,
        "aud": "https://appleid.apple.com",
        "sub": client_id,
    }
    headers = {"kid": key_id, "alg": "ES256"}
    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)


def _exchange_apple_code(code: str) -> str:
    client_id = getattr(settings, "APPLE_CLIENT_ID", "").strip()
    redirect_uri = getattr(settings, "APPLE_REDIRECT_URI", "").strip()
    client_secret = _apple_client_secret_jwt()
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://appleid.apple.com/auth/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            tok = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"Apple token exchange failed: {err}") from exc
    id_token = tok.get("id_token")
    if not id_token:
        raise ValueError("Apple response missing id_token")
    parts = id_token.split(".")
    if len(parts) < 2:
        raise ValueError("Invalid Apple id_token")
    pad = "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode("utf-8"))
    email = payload.get("email")
    if not email:
        raise ValueError("Apple did not include email (first sign-in only includes email in id_token).")
    return str(email).strip().lower()


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
    if provider == "facebook":
        query = urllib.parse.urlencode(
            {
                "client_id": getattr(settings, "FACEBOOK_CLIENT_ID", ""),
                "redirect_uri": getattr(settings, "FACEBOOK_REDIRECT_URI", ""),
                "scope": "email,public_profile",
                "state": state,
                "response_type": "code",
            }
        )
        return f"https://www.facebook.com/v21.0/dialog/oauth?{query}"
    if provider == "apple":
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": getattr(settings, "APPLE_CLIENT_ID", ""),
                "redirect_uri": getattr(settings, "APPLE_REDIRECT_URI", ""),
                "scope": "name email",
                "state": state,
                "response_mode": "query",
            }
        )
        return f"https://appleid.apple.com/auth/authorize?{query}"
    raise ValueError("Unsupported provider")


class OtpRequestView(RatelimitedAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_send"
    ratelimit_rate = "10/m"

    def post(self, request):
        identity = str(request.data.get("identity") or "").strip()
        if not identity:
            return Response({"detail": "identity is required"}, status=status.HTTP_400_BAD_REQUEST)

        channel = "email" if _is_email(identity) else "phone"
        if channel == "email":
            try:
                normalized_identity = EmailValidationService.validate(identity)
            except AuthServiceError as exc:
                return Response(
                    exc.as_validation_dict() if exc.field else {"detail": exc.detail},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                _, dev_code = OtpService.issue(
                    email=normalized_identity,
                    purpose=OtpPurpose.LOGIN,
                    send_email=True,
                )
            except AuthServiceError as exc:
                return Response({"detail": exc.detail}, status=exc.status_code)
            return Response(
                {
                    "detail": "Verification code sent to your email.",
                    "channel": channel,
                    "dev_code": dev_code,
                },
                status=status.HTTP_200_OK,
            )

        normalized_identity = _normalize_phone(identity)
        if not normalized_identity:
            return Response({"detail": "Invalid phone number"}, status=status.HTTP_400_BAD_REQUEST)
        otp = OtpService.generate_code()
        cache.set(_otp_cache_key(channel, normalized_identity), otp, timeout=OtpService.expiry_seconds())
        logger.info("TRAK OTP for phone %s is %s", normalized_identity, otp)
        return Response(
            {
                "detail": "Verification code sent to your phone.",
                "channel": channel,
                "dev_code": otp if settings.DEBUG else None,
            },
            status=status.HTTP_200_OK,
        )


class OtpVerifyView(RatelimitedAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_verify"
    ratelimit_rate = "20/m"

    def post(self, request):
        identity = str(request.data.get("identity") or "").strip()
        code = str(request.data.get("code") or "").strip()
        if not identity or not code:
            return Response({"detail": "identity and code are required"}, status=status.HTTP_400_BAD_REQUEST)

        channel = "email" if _is_email(identity) else "phone"
        ip = AuthSecurityService.client_ip(request)
        if channel == "email":
            normalized_identity = EmailValidationService.normalize(identity)
            try:
                AuthSecurityService.check_otp_verify_allowed(
                    normalized_identity, OtpPurpose.LOGIN, ip
                )
                OtpService.verify(
                    email=normalized_identity,
                    purpose=OtpPurpose.LOGIN,
                    code=code,
                )
                AuthSecurityService.clear_otp_verify_failures(
                    normalized_identity, OtpPurpose.LOGIN, ip
                )
            except BruteForceLockoutError as exc:
                return Response({"detail": exc.detail}, status=exc.status_code)
            except OtpError as exc:
                AuthSecurityService.record_otp_verify_failure(
                    normalized_identity, OtpPurpose.LOGIN, ip
                )
                return Response({"detail": exc.detail}, status=exc.status_code)
            except AuthServiceError as exc:
                return Response({"detail": exc.detail}, status=exc.status_code)
        else:
            normalized_identity = _normalize_phone(identity)
            cache_key = _otp_cache_key(channel, normalized_identity)
            expected = cache.get(cache_key)
            if not expected or expected != code:
                return Response(
                    {"detail": "Invalid or expired verification code."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cache.delete(cache_key)

        if channel == "email":
            email = normalized_identity
            user = User.objects.filter(email=email).first()
            if not user:
                user = User.objects.create_user(email=email, password=_random_user_password())
        else:
            # Login should bind to an existing profile using this phone number.
            try:
                profile_row = _profile_collection().find_one({"phone": normalized_identity})
            except Exception:
                logger.exception("Mongo lookup failed during OTP phone login")
                return Response(
                    {"detail": "Profile service is temporarily unavailable. Please try again shortly."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            if not profile_row:
                return Response({"detail": "No account is linked to this phone number."}, status=status.HTTP_400_BAD_REQUEST)
            linked_user_id = profile_row.get("user_id")
            if not linked_user_id:
                return Response({"detail": "Invalid phone profile mapping."}, status=status.HTTP_400_BAD_REQUEST)
            user = User.objects.filter(pk=linked_user_id).first()
            if not user:
                return Response({"detail": "Account linked to this phone no longer exists."}, status=status.HTTP_400_BAD_REQUEST)
        if channel == "phone":
            try:
                _profile_collection().update_one(
                    {"user_id": user.pk},
                    {"$set": {"phone": normalized_identity, "phone_verified": True}},
                    upsert=True,
                )
            except Exception:
                logger.exception("Mongo update failed during OTP phone verification for user_id=%s", user.pk)
        else:
            _mark_email_verified(user)

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
            {"id": "apple", "name": "Apple", "enabled": bool(getattr(settings, "APPLE_CLIENT_ID", ""))},
            {"id": "facebook", "name": "Facebook", "enabled": bool(getattr(settings, "FACEBOOK_CLIENT_ID", ""))},
            {"id": "github", "name": "GitHub", "enabled": bool(getattr(settings, "GITHUB_CLIENT_ID", ""))},
            {"id": "twitter", "name": "Twitter/X", "enabled": False},
        ]
        return Response({"providers": providers})


class SocialStartView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, provider: str):
        provider = provider.strip().lower()
        if provider not in {"google", "github", "facebook", "apple"}:
            return Response({"detail": "Unsupported provider"}, status=status.HTTP_400_BAD_REQUEST)
        if provider == "facebook" and not getattr(settings, "FACEBOOK_CLIENT_ID", "").strip():
            return Response({"detail": "Facebook OAuth is not configured."}, status=status.HTTP_400_BAD_REQUEST)
        if provider == "apple" and not getattr(settings, "APPLE_CLIENT_ID", "").strip():
            return Response({"detail": "Apple Sign-In is not configured."}, status=status.HTTP_400_BAD_REQUEST)
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
        if provider not in {"google", "github", "facebook", "apple"}:
            return Response({"detail": "Unsupported provider"}, status=status.HTTP_400_BAD_REQUEST)
        if not state or cache.get(_social_state_cache_key(state)) != provider:
            return Response({"detail": "Invalid social state"}, status=status.HTTP_400_BAD_REQUEST)
        cache.delete(_social_state_cache_key(state))
        if not code:
            return Response({"detail": "Missing social auth code"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if provider == "google":
                email = _exchange_google_code(code)
            elif provider == "github":
                email = _exchange_github_code(code)
            elif provider == "facebook":
                email = _exchange_facebook_code(code)
            else:
                email = _exchange_apple_code(code)
        except Exception as exc:
            logger.exception("Social callback failed for %s", provider)
            return Response({"detail": f"Social login failed: {exc}"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email).first()
        if not user:
            user = User.objects.create_user(email=email, password=_random_user_password())

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


class FirebaseLoginView(APIView):
    """POST { id_token } — verify Firebase ID token and return TRAK JWT (get-or-create user by email)."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        id_token = str(request.data.get("id_token") or "").strip()
        if not id_token:
            return Response({"detail": "id_token is required."}, status=status.HTTP_400_BAD_REQUEST)
        from notifications.fcm import _ensure_app

        if _ensure_app() is None:
            return Response(
                {"detail": "Firebase is not configured on the server. Set FIREBASE_CREDENTIALS_JSON."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            from firebase_admin import auth as firebase_auth

            decoded = firebase_auth.verify_id_token(id_token)
        except Exception as exc:
            logger.exception("Firebase ID token verification failed")
            return Response({"detail": f"Invalid Firebase token: {exc}"}, status=status.HTTP_400_BAD_REQUEST)
        email = str(decoded.get("email") or "").strip().lower()
        if not email:
            return Response(
                {"detail": "Firebase token has no email. Ensure the provider returns email to Firebase."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = User.objects.filter(email__iexact=email).first()
        is_new_user = user is None
        prev_last_login = user.last_login if user else None
        if is_new_user:
            user = User.objects.create_user(email=email, password=_random_user_password())
        refresh = RefreshToken.for_user(user)
        if not is_new_user:
            try:
                now = timezone.now()
                User.objects.filter(pk=user.pk).update(last_login=now)
                user.last_login = now
            except Exception:
                pass
            try:
                from notifications.reengagement import maybe_welcome_back_notification

                maybe_welcome_back_notification(user, previous_last_login=prev_last_login)
            except Exception:
                pass
        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": _user_payload(user),
                "is_new_user": is_new_user,
                "onboarding_complete": _onboarding_complete(user.pk),
            },
            status=status.HTTP_200_OK,
        )


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
        if provider not in {"google", "github", "twitter", "facebook", "apple"}:
            return Response({"detail": "Unsupported social provider"}, status=status.HTTP_400_BAD_REQUEST)
        if not email or not _is_email(email):
            return Response({"detail": "A valid email is required"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email=email).first()
        if not user:
            user = User.objects.create_user(email=email, password=_random_user_password())
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


class RegisterView(RatelimitedAPIMixin, APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"
    ratelimit_rate = "10/h"

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        full_name = str(request.data.get("full_name") or "").strip()
        phone = _normalize_phone(str(request.data.get("phone") or ""))
        email_prefix = (user.email or "").split("@")[0]
        try:
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
        except Exception:
            # Do not fail account creation if Mongo profile sync is temporarily down.
            logger.exception("Mongo profile sync failed during registration for user_id=%s", user.pk)

        dev_code = None
        if getattr(settings, "REGISTER_SEND_VERIFICATION_OTP", True):
            try:
                _, dev_code = OtpService.issue(
                    email=user.email,
                    purpose=OtpPurpose.EMAIL_VERIFICATION,
                    user=user,
                    send_email=True,
                )
            except Exception:
                logger.exception("Failed to send registration verification OTP for %s", user.email)

        refresh = RefreshToken.for_user(user)
        payload = {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user": _user_payload(user),
            "verification_required": not user.email_verified,
            "is_new_user": True,
            "onboarding_complete": False,
        }
        if settings.DEBUG and dev_code:
            payload["dev_code"] = dev_code
        return Response(payload, status=status.HTTP_201_CREATED)


class LoginView(RatelimitedAPIMixin, TokenObtainPairView):
    permission_classes = [permissions.AllowAny]
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
    ratelimit_rate = "30/m"

    def post(self, request, *args, **kwargs):
        raw_email = str(request.data.get("email") or request.data.get("username") or "").strip()
        ip = AuthSecurityService.client_ip(request)
        email = raw_email.lower()
        try:
            if raw_email:
                email = EmailValidationService.validate(raw_email)
        except AuthServiceError:
            pass
        try:
            AuthSecurityService.check_login_allowed(email, ip)
        except BruteForceLockoutError as exc:
            return Response({"detail": exc.detail}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        response = super().post(request, *args, **kwargs)
        if response.status_code >= 400:
            if email:
                AuthSecurityService.record_login_failure(email, ip)
        else:
            AuthSecurityService.clear_login_failures(email, ip)
        return response


class ThrottledTokenRefreshView(TokenRefreshView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "refresh"


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

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
        try:
            _profile_collection().update_one({"user_id": request.user.pk}, {"$set": payload}, upsert=True)
        except Exception:
            logger.exception("Mongo update failed for profile patch user_id=%s", request.user.pk)
            return Response(
                {"detail": "Profile service is temporarily unavailable. Please try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(_user_payload(request.user), status=status.HTTP_200_OK)


class VerifyContactRequestView(RatelimitedAPIMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_send"
    ratelimit_rate = "10/m"

    def post(self, request):
        channel = str(request.data.get("channel") or "").strip().lower()
        if channel not in {"email", "phone"}:
            return Response({"detail": "channel must be email or phone"}, status=status.HTTP_400_BAD_REQUEST)
        profile = _get_profile(request.user.pk)
        if channel == "email":
            identity = request.user.email
            try:
                _, dev_code = OtpService.issue(
                    email=identity,
                    purpose=OtpPurpose.CONTACT_VERIFY,
                    user=request.user,
                    send_email=True,
                )
            except AuthServiceError as exc:
                return Response({"detail": exc.detail}, status=exc.status_code)
            return Response(
                {
                    "detail": "Verification code sent.",
                    "channel": channel,
                    "dev_code": dev_code if settings.DEBUG else None,
                },
                status=status.HTTP_200_OK,
            )
        else:
            identity = _normalize_phone(str(request.data.get("phone") or profile.get("phone") or ""))
            if not identity:
                return Response({"detail": "Phone is required to verify phone."}, status=status.HTTP_400_BAD_REQUEST)
            try:
                _profile_collection().update_one({"user_id": request.user.pk}, {"$set": {"phone": identity}}, upsert=True)
            except Exception:
                logger.exception("Mongo update failed during verify request for user_id=%s", request.user.pk)
                return Response(
                    {"detail": "Profile service is temporarily unavailable. Please try again shortly."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        otp = OtpService.generate_code()
        cache.set(_otp_cache_key(channel, identity), otp, timeout=OtpService.expiry_seconds())
        logger.info("TRAK verify OTP for phone %s is %s", identity, otp)
        return Response(
            {"detail": "Verification code sent.", "channel": channel, "dev_code": otp if settings.DEBUG else None},
            status=status.HTTP_200_OK,
        )


class VerifyContactConfirmView(RatelimitedAPIMixin, APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_verify"
    ratelimit_rate = "20/m"

    def post(self, request):
        channel = str(request.data.get("channel") or "").strip().lower()
        code = str(request.data.get("code") or "").strip()
        if channel not in {"email", "phone"}:
            return Response({"detail": "channel must be email or phone"}, status=status.HTTP_400_BAD_REQUEST)
        if not code:
            return Response({"detail": "code is required"}, status=status.HTTP_400_BAD_REQUEST)
        profile = _get_profile(request.user.pk)
        ip = AuthSecurityService.client_ip(request)
        if channel == "email":
            try:
                AuthSecurityService.check_otp_verify_allowed(
                    request.user.email, OtpPurpose.CONTACT_VERIFY, ip
                )
                OtpService.verify(
                    email=request.user.email,
                    purpose=OtpPurpose.CONTACT_VERIFY,
                    code=code,
                )
                AuthSecurityService.clear_otp_verify_failures(
                    request.user.email, OtpPurpose.CONTACT_VERIFY, ip
                )
                _mark_email_verified(request.user)
            except (BruteForceLockoutError, OtpError, AuthServiceError) as exc:
                if isinstance(exc, OtpError):
                    AuthSecurityService.record_otp_verify_failure(
                        request.user.email, OtpPurpose.CONTACT_VERIFY, ip
                    )
                status_code = getattr(exc, "status_code", status.HTTP_400_BAD_REQUEST)
                body = exc.as_validation_dict() if hasattr(exc, "as_validation_dict") and exc.field else {"detail": exc.detail}
                return Response(body, status=status_code)
        else:
            identity = _normalize_phone(str(profile.get("phone") or ""))
            if not identity:
                return Response({"detail": "No phone available for verification."}, status=status.HTTP_400_BAD_REQUEST)
            expected = cache.get(_otp_cache_key(channel, identity))
            if not expected or expected != code:
                return Response({"detail": "Invalid or expired verification code."}, status=status.HTTP_400_BAD_REQUEST)
            cache.delete(_otp_cache_key(channel, identity))
            try:
                _profile_collection().update_one(
                    {"user_id": request.user.pk},
                    {"$set": {"phone_verified": True}},
                    upsert=True,
                )
            except Exception:
                logger.exception("Mongo update failed during verify confirm for user_id=%s", request.user.pk)
                return Response(
                    {"detail": "Profile service is temporarily unavailable. Please try again shortly."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        request.user.refresh_from_db()
        return Response(_user_payload(request.user), status=status.HTTP_200_OK)


def _otp_preview_enabled() -> bool:
    return bool(getattr(settings, "OTP_DEV_PREVIEW", False))


class PasswordResetCheckEmailView(RatelimitedAPIMixin, APIView):
    """POST { email } — returns whether an account exists (for client UX)."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"
    ratelimit_rate = "30/m"

    def post(self, request):
        ser = PasswordResetRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data["email"]
        exists = User.objects.filter(email__iexact=email).exists()
        return Response({"exists": exists}, status=status.HTTP_200_OK)


class PasswordResetRequestView(RatelimitedAPIMixin, APIView):
    """POST { email } — generic message; sends a 6-digit OTP to email when account exists."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"
    ratelimit_rate = "30/m"

    def post(self, request):
        try:
            ser = PasswordResetRequestSerializer(data=request.data)
            ser.is_valid(raise_exception=True)
            email = ser.validated_data["email"]
            user = User.objects.filter(email=email).first()
            payload: dict = {
                "detail": "If an account exists for this address, a reset code was sent."
            }
            if user is not None and user.is_active:
                try:
                    purpose_label = OtpPurpose.LABELS.get(
                        OtpPurpose.PASSWORD_RESET, "password reset"
                    )
                    _, plain_code = OtpService.issue(
                        email=email,
                        purpose=OtpPurpose.PASSWORD_RESET,
                        user=user,
                        send_email=False,
                        check_mx=False,
                    )
                    email_sent, email_error = send_otp_email_sync(
                        to_email=email,
                        code=plain_code,
                        purpose_label=purpose_label,
                        expires_minutes=max(1, OtpService.expiry_seconds() // 60),
                    )
                    payload["email_sent"] = email_sent
                    if email_error:
                        payload["email_error"] = email_error
                    if not email_sent:
                        payload["detail"] = (
                            "We could not send the reset email right now. Please try again in a few minutes."
                        )
                        if email_error in {"smtp_daily_limit", "gmail_daily_limit"}:
                            payload["detail"] = (
                                "Email service is temporarily unavailable. Please try again later."
                            )
                except Exception:
                    logger.exception("Password reset OTP failed for %s", email)
                    payload["email_sent"] = False
            return Response(payload, status=status.HTTP_200_OK)
        except APIException:
            raise
        except Exception:
            logger.exception("Password reset request failed")
            return Response(
                {"detail": "Could not process password reset. Please try again later."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class PasswordResetOtpVerifyView(RatelimitedAPIMixin, APIView):
    """POST { email, code } — verify OTP only; returns short-lived reset_token."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"
    ratelimit_rate = "30/m"

    def post(self, request):
        ser = PasswordResetOtpVerifySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data["email"]
        code = ser.validated_data["code"]
        ip = AuthSecurityService.client_ip(request)
        try:
            AuthSecurityService.check_otp_verify_allowed(
                email, OtpPurpose.PASSWORD_RESET, ip
            )
            OtpService.verify(email=email, purpose=OtpPurpose.PASSWORD_RESET, code=code)
            AuthSecurityService.clear_otp_verify_failures(
                email, OtpPurpose.PASSWORD_RESET, ip
            )
        except BruteForceLockoutError as exc:
            return Response({"detail": exc.detail}, status=exc.status_code)
        except OtpError:
            AuthSecurityService.record_otp_verify_failure(
                email, OtpPurpose.PASSWORD_RESET, ip
            )
            return Response(
                {"detail": "Invalid or expired reset code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = User.objects.filter(email=email).first()
        if not user or not user.is_active:
            return Response(
                {"detail": "Invalid or expired reset code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reset_token = issue_password_reset_session(email)
        return Response(
            {
                "detail": "Code verified. You can set a new password.",
                "reset_token": reset_token,
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetOtpConfirmView(RatelimitedAPIMixin, APIView):
    """POST { email, reset_token, password, password_confirm } — set password after OTP verify."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"
    ratelimit_rate = "10/h"

    def post(self, request):
        ser = PasswordResetOtpConfirmSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        email = ser.validated_data["email"]
        code = ser.validated_data.get("code") or ""
        reset_token = ser.validated_data.get("reset_token") or ""
        ip = AuthSecurityService.client_ip(request)

        if reset_token:
            if not consume_password_reset_session(email=email, reset_token=reset_token):
                return Response(
                    {"detail": "Reset session expired. Verify your code again."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            try:
                AuthSecurityService.check_otp_verify_allowed(
                    email, OtpPurpose.PASSWORD_RESET, ip
                )
                OtpService.verify(
                    email=email, purpose=OtpPurpose.PASSWORD_RESET, code=code
                )
                AuthSecurityService.clear_otp_verify_failures(
                    email, OtpPurpose.PASSWORD_RESET, ip
                )
            except BruteForceLockoutError as exc:
                return Response({"detail": exc.detail}, status=exc.status_code)
            except OtpError:
                AuthSecurityService.record_otp_verify_failure(
                    email, OtpPurpose.PASSWORD_RESET, ip
                )
                return Response(
                    {"detail": "Invalid or expired reset code."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        user = User.objects.filter(email=email).first()
        if not user or not user.is_active:
            return Response(
                {"detail": "Invalid or expired reset code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(ser.validated_data["password"])
        user.save()
        AuthSecurityService.clear_login_failures(email, ip)
        return Response(
            {"detail": "Password has been reset. You can sign in with your new password."},
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


class FollowView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        target_user_id = str(request.data.get("user_id") or "").strip()
        if not target_user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        if target_user_id == str(request.user.pk):
            return Response({"detail": "You cannot follow yourself."}, status=status.HTTP_400_BAD_REQUEST)
        target_user = User.objects.filter(pk=target_user_id).first()
        if not target_user:
            return Response({"detail": "Target user not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            _follow_collection().update_one(
                {"follower_user_id": request.user.pk, "followed_user_id": target_user_id},
                {"$set": {"follower_user_id": request.user.pk, "followed_user_id": target_user_id}},
                upsert=True,
            )
        except Exception:
            logger.exception("Mongo update failed during follow action user_id=%s", request.user.pk)
            return Response(
                {"detail": "Follow service is temporarily unavailable. Please try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
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
        target_user_id = str(
            request.data.get("user_id") or request.query_params.get("user_id") or ""
        ).strip()
        if not target_user_id:
            return Response({"detail": "user_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            _follow_collection().delete_one({"follower_user_id": request.user.pk, "followed_user_id": target_user_id})
        except Exception:
            logger.exception("Mongo delete failed during unfollow action user_id=%s", request.user.pk)
            return Response(
                {"detail": "Follow service is temporarily unavailable. Please try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
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
