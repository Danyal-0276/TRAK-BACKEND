"""
Django settings for TRAK_Backend project.

Stack: Django 5.2 LTS + django-mongodb-backend (Mongo as primary DB for
auth/sessions/contenttypes). Raw news articles still use plain pymongo against
a separate database so collections don't intermix with Django-managed ones.
"""

import os
from datetime import timedelta
from pathlib import Path

import django_mongodb_backend
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

# Runtime configuration: read env vars only in this file (see .env.example).
# Application code should use `from django.conf import settings`.

# Prefer MONGODB_URI_DIRECT when campus/corporate DNS blocks mongodb+srv SRV lookups.
MONGODB_URI = (
    os.environ.get("MONGODB_URI_DIRECT", "").strip()
    or os.environ.get("MONGODB_URI", "").strip()
)
if not MONGODB_URI:
    raise RuntimeError(
        "MONGODB_URI must be set. Use mongodb://127.0.0.1:27017 for local MongoDB, "
        "or Atlas 'Standard connection string' if mongodb+srv DNS times out."
    )


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/3.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-pu50tf0wn-ug=15%$i9_^7dune*_q5wff%w&&4h6pi3!5n(7b#",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() in ("1", "true", "yes")

_allowed = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").strip()
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(",") if h.strip()]
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
if _render_host and _render_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_render_host)

# Local mobile app (Wi‑Fi / emulator) — avoid 400 Invalid HTTP_HOST on LAN IP.
if DEBUG:
    for _dev_host in ("10.0.2.2", "0.0.0.0"):
        if _dev_host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_dev_host)
    try:
        import socket

        _sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _sock.connect(("8.8.8.8", 80))
        _lan_ip = _sock.getsockname()[0]
        _sock.close()
        if _lan_ip and _lan_ip not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_lan_ip)
    except OSError:
        pass

AUTH_USER_MODEL = "accounts.User"

# Comma-separated emails → role admin on registration.
# Three built-in admins (Danyal, Shahroz, Abdullah) are always included; extend with ADMIN_EMAILS.
_BUILTIN_ADMIN_EMAILS = (
    "danyal@admin.com,shahroz@admin.com,abdullah@admin.com"
)
# Used by seed_default_admins only (shared initial password).
BUILTIN_ADMIN_EMAILS_LIST = [
    e.strip().lower() for e in _BUILTIN_ADMIN_EMAILS.split(",") if e.strip()
]
_extra_admin_emails = os.environ.get("ADMIN_EMAILS", "").strip()
ADMIN_EMAILS = (
    f"{_BUILTIN_ADMIN_EMAILS},{_extra_admin_emails}"
    if _extra_admin_emails
    else _BUILTIN_ADMIN_EMAILS
)


# Application definition

INSTALLED_APPS = [
    # Django contrib apps overridden to use ObjectIdAutoField as PK (required by django-mongodb-backend).
    'TRAK_Backend.mongo_apps.MongoAdminConfig',
    'TRAK_Backend.mongo_apps.MongoAuthConfig',
    'TRAK_Backend.mongo_apps.MongoContentTypesConfig',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Project apps
    'accounts.apps.AccountsConfig',
    'news.apps.NewsConfig',
    'notifications.apps.NotificationsConfig',
    'admin_panel.apps.AdminPanelConfig',
    # Third party apps
    'rest_framework',
    'corsheaders',
    'channels',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'TRAK_Backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'TRAK_Backend.wsgi.application'
ASGI_APPLICATION = "TRAK_Backend.asgi.application"


# Database — MongoDB via django-mongodb-backend.
# Auth/sessions/contenttypes/User live in the Django-managed Mongo database.
# Raw scraped articles live in a separate database (settings.MONGODB_RAW_DATABASE)
# accessed directly through pymongo (see news/mongo_db.py).

MONGODB_DJANGO_DATABASE = os.environ.get("MONGODB_DJANGO_DATABASE", "trak_django").strip() or "trak_django"

DATABASES = {
    "default": django_mongodb_backend.parse_uri(
        MONGODB_URI,
        db_name=MONGODB_DJANGO_DATABASE,
    ),
}

# Contrib apps store migrations under vendor Django paths by default; override so
# MongoDB-compatible migrations live in this repo (required for ObjectIdAutoField).
# See https://django-mongodb-backend.readthedocs.io/en/latest/intro/configure/#configuring-migrations
MIGRATION_MODULES = {
    "admin": "TRAK_Backend.mongo_migrations.admin",
    "auth": "TRAK_Backend.mongo_migrations.auth",
    "contenttypes": "TRAK_Backend.mongo_migrations.contenttypes",
}

# Password validation
# https://docs.djangoproject.com/en/3.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/3.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Default primary key field type
# https://docs.djangoproject.com/en/3.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django_mongodb_backend.fields.ObjectIdAutoField'

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "EXCEPTION_HANDLER": "TRAK_Backend.api_exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "register": os.environ.get("THROTTLE_REGISTER", "10/hour"),
        "login": os.environ.get("THROTTLE_LOGIN", "30/hour"),
        "refresh": os.environ.get("THROTTLE_REFRESH", "120/hour"),
        "password_reset": os.environ.get("THROTTLE_PASSWORD_RESET", "5/hour"),
        "otp_send": os.environ.get("THROTTLE_OTP_SEND", "10/hour"),
        "otp_verify": os.environ.get("THROTTLE_OTP_VERIFY", "30/hour"),
        "email_validate": os.environ.get("THROTTLE_EMAIL_VALIDATE", "60/hour"),
    },
}

_redis_url = os.environ.get("REDIS_URL", "").strip()

# --- Auth security (email validation, OTP, brute-force) ---
RATELIMIT_ENABLE = os.environ.get("RATELIMIT_ENABLE", "true").lower() in ("1", "true", "yes")
RATELIMIT_USE_CACHE = "default"

EMAIL_VALIDATION = {
    "CHECK_MX": os.environ.get("EMAIL_VALIDATION_CHECK_MX", "true").lower() in ("1", "true", "yes"),
    "BLOCK_DISPOSABLE": os.environ.get("EMAIL_VALIDATION_BLOCK_DISPOSABLE", "true").lower()
    in ("1", "true", "yes"),
    "BLOCKED_DOMAINS": [
        d.strip().lower()
        for d in os.environ.get("EMAIL_VALIDATION_BLOCKED_DOMAINS", "").split(",")
        if d.strip()
    ],
    "DISPOSABLE_EXTRA": [
        d.strip().lower()
        for d in os.environ.get("EMAIL_VALIDATION_DISPOSABLE_EXTRA", "").split(",")
        if d.strip()
    ],
}

OTP = {
    "EXPIRY_SECONDS": int(os.environ.get("OTP_EXPIRY_SECONDS", "300")),
    "RESEND_COOLDOWN_SECONDS": int(os.environ.get("OTP_RESEND_COOLDOWN_SECONDS", "60")),
    "MAX_ATTEMPTS": int(os.environ.get("OTP_MAX_ATTEMPTS", "5")),
}

OTP_HASH_SECRET = os.environ.get("OTP_HASH_SECRET", "").strip() or None

AUTH_SECURITY = {
    "LOGIN_MAX_ATTEMPTS": int(os.environ.get("AUTH_LOGIN_MAX_ATTEMPTS", "10")),
    "LOGIN_LOCKOUT_SECONDS": int(os.environ.get("AUTH_LOGIN_LOCKOUT_SECONDS", "900")),
    "OTP_VERIFY_MAX_ATTEMPTS": int(os.environ.get("AUTH_OTP_VERIFY_MAX_ATTEMPTS", "5")),
    "OTP_VERIFY_LOCKOUT_SECONDS": int(os.environ.get("AUTH_OTP_VERIFY_LOCKOUT_SECONDS", "900")),
}

REGISTER_SEND_VERIFICATION_OTP = os.environ.get(
    "REGISTER_SEND_VERIFICATION_OTP", "true"
).lower() in ("1", "true", "yes")

# Redis cache only when explicitly enabled (avoids 500s if Redis is not running locally).
_use_redis = os.environ.get("USE_REDIS", "false").lower() in ("1", "true", "yes")
if _redis_url and _use_redis:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "trak-auth-cache",
        }
    }

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "accounts.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "accounts.otp": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "accounts.email": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

CORS_ALLOW_ALL_ORIGINS = os.environ.get(
    "CORS_ALLOW_ALL_ORIGINS",
    "true" if DEBUG else "false",
).lower() in (
    "1",
    "true",
    "yes",
)
_cors_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
if _cors_origins:
    CORS_ALLOW_ALL_ORIGINS = False
    CORS_ALLOWED_ORIGINS = [o.strip() for o in _cors_origins.split(",") if o.strip()]

# Local Vite (port 3000/5173) — login/signup fail in the browser if these are missing.
if DEBUG and not CORS_ALLOW_ALL_ORIGINS:
    _local_dev_cors = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    if _cors_origins:
        CORS_ALLOWED_ORIGINS = list(
            dict.fromkeys([*CORS_ALLOWED_ORIGINS, *_local_dev_cors])
        )
    else:
        CORS_ALLOWED_ORIGINS = _local_dev_cors

_csrf_trusted_origins = os.environ.get("CSRF_TRUSTED_ORIGINS", "").strip()
if _csrf_trusted_origins:
    CSRF_TRUSTED_ORIGINS = [
        o.strip() for o in _csrf_trusted_origins.split(",") if o.strip()
    ]
elif _render_host:
    CSRF_TRUSTED_ORIGINS = [f"https://{_render_host}"]

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=int(os.environ.get("JWT_ACCESS_MINUTES", "60"))
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=int(os.environ.get("JWT_REFRESH_DAYS", "7"))
    ),
    "ROTATE_REFRESH_TOKENS": True,
    # token_blacklist app is not installed (its models use integer PKs; incompatible
    # with django-mongodb-backend ObjectId PKs without custom migrations).
    "BLACKLIST_AFTER_ROTATION": False,
}

if _redis_url and _use_redis:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [_redis_url]},
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }

ALLOW_DEMO_SOCIAL_LOGIN = os.environ.get("ALLOW_DEMO_SOCIAL_LOGIN", "false").lower() in ("1", "true", "yes")

# --- Email (password reset). Dev default: print emails to console. ---
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "20"))
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "TRAK <noreply@trak.local>")
# Full URL to the web (or universal) reset screen, e.g. https://app.example.com/reset-password
PASSWORD_RESET_FRONTEND_URL = os.environ.get(
    "PASSWORD_RESET_FRONTEND_URL",
    "http://127.0.0.1:5173/reset-password",
).strip()
SOCIAL_AUTH_FRONTEND_URL = os.environ.get(
    "SOCIAL_AUTH_FRONTEND_URL",
    "http://127.0.0.1:5173/login",
).strip()

# OAuth providers
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI",
    "http://127.0.0.1:8000/api/auth/social/google/callback/",
).strip()
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "").strip()
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()
GITHUB_REDIRECT_URI = os.environ.get(
    "GITHUB_REDIRECT_URI",
    "http://127.0.0.1:8000/api/auth/social/github/callback/",
).strip()

FACEBOOK_CLIENT_ID = os.environ.get("FACEBOOK_CLIENT_ID", "").strip()
FACEBOOK_CLIENT_SECRET = os.environ.get("FACEBOOK_CLIENT_SECRET", "").strip()
FACEBOOK_REDIRECT_URI = os.environ.get(
    "FACEBOOK_REDIRECT_URI",
    "http://127.0.0.1:8000/api/auth/social/facebook/callback/",
).strip()

# Sign in with Apple (OAuth web flow). APPLE_PRIVATE_KEY = .p8 contents with \n for newlines.
APPLE_CLIENT_ID = os.environ.get("APPLE_CLIENT_ID", "").strip()
APPLE_TEAM_ID = os.environ.get("APPLE_TEAM_ID", "").strip()
APPLE_KEY_ID = os.environ.get("APPLE_KEY_ID", "").strip()
APPLE_PRIVATE_KEY = os.environ.get("APPLE_PRIVATE_KEY", "").strip()
APPLE_REDIRECT_URI = os.environ.get(
    "APPLE_REDIRECT_URI",
    "http://127.0.0.1:8000/api/auth/social/apple/callback/",
).strip()

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    if os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "true").lower() in ("1", "true", "yes"):
        SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = (
        os.environ.get("SECURE_HSTS_INCLUDE_SUBDOMAINS", "true").lower() in ("1", "true", "yes")
    )
    SECURE_HSTS_PRELOAD = (
        os.environ.get("SECURE_HSTS_PRELOAD", "false").lower() in ("1", "true", "yes")
    )
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_REFERRER_POLICY = os.environ.get("SECURE_REFERRER_POLICY", "same-origin")

# --- Raw news scrapers (pymongo collection `raw_articles` in TRAK_DB) ---
MONGODB_RAW_DATABASE = os.environ.get("MONGODB_RAW_DATABASE", "TRAK_DB")
MONGODB_RAW_COLLECTION = os.environ.get("MONGODB_RAW_COLLECTION", "raw_articles")
MONGODB_PROCESSED_COLLECTION = os.environ.get("MONGODB_PROCESSED_COLLECTION", "processed_articles")
MONGODB_USER_KEYWORDS_COLLECTION = os.environ.get("MONGODB_USER_KEYWORDS_COLLECTION", "user_keywords")
MONGODB_CHATBOT_HISTORY_COLLECTION = os.environ.get("MONGODB_CHATBOT_HISTORY_COLLECTION", "chatbot_history")

# TRAK news chatbot — Google Gemini 1.5 Flash (news-only; set key in production)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip() or None
GEMINI_CHATBOT_MODEL = os.environ.get("GEMINI_CHATBOT_MODEL", "gemini-1.5-flash").strip() or "gemini-1.5-flash"
GEMINI_CHATBOT_TIMEOUT = float(os.environ.get("GEMINI_CHATBOT_TIMEOUT", "45"))
GEMINI_CHATBOT_FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get("GEMINI_CHATBOT_FALLBACK_MODELS", "").split(",")
    if m.strip()
]
MONGODB_NOTIFICATIONS_COLLECTION = os.environ.get("MONGODB_NOTIFICATIONS_COLLECTION", "notifications")
MONGODB_DEVICE_TOKENS_COLLECTION = os.environ.get("MONGODB_DEVICE_TOKENS_COLLECTION", "device_tokens")
MONGODB_USER_PREFERENCES_COLLECTION = os.environ.get("MONGODB_USER_PREFERENCES_COLLECTION", "user_preferences")
MONGODB_BOOKMARKS_COLLECTION = os.environ.get("MONGODB_BOOKMARKS_COLLECTION", "bookmarks")
MONGODB_REACTIONS_COLLECTION = os.environ.get("MONGODB_REACTIONS_COLLECTION", "reactions")

# Optional: directory with HuggingFace-style saved model for 3-class credibility (real/fake/suspicious)
CREDIBILITY_MODEL_PATH = os.environ.get("CREDIBILITY_MODEL_PATH", "").strip() or None
CREDIBILITY_CONFIDENCE_THRESHOLD = float(os.environ.get("CREDIBILITY_CONFIDENCE_THRESHOLD", "0.6"))

# Hugging Face Spaces + token (private Spaces)
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip() or None
FAKE_DETECTION_SPACE_ID = os.environ.get("FAKE_DETECTION_SPACE_ID", "").strip() or None
FAKE_DETECTION_SPACE_API_NAME = (
    os.environ.get("FAKE_DETECTION_SPACE_API_NAME", "/detect").strip() or "/detect"
)
SUMMARIZER_SPACE_ID = os.environ.get("SUMMARIZER_SPACE_ID", "").strip() or None
SUMMARIZER_SPACE_API_NAME = (
    os.environ.get("SUMMARIZER_SPACE_API_NAME", "/summarize").strip() or "/summarize"
)

# Fact checker — multi-provider second pass after fake-detection Space
FACT_CHECKER_ENABLED = os.environ.get("FACT_CHECKER_ENABLED", "true").strip()
# Comma-separated: wikipedia, wikidata, openalex (all free). Optional: google (needs API key)
FACT_CHECKER_PROVIDERS = os.environ.get(
    "FACT_CHECKER_PROVIDERS", "wikipedia,wikidata,openalex"
).strip()
FACT_CHECKER_PROVIDER = os.environ.get("FACT_CHECKER_PROVIDER", "wikipedia").strip()
GOOGLE_FACT_CHECK_API_KEY = os.environ.get("GOOGLE_FACT_CHECK_API_KEY", "").strip() or None
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "trak@example.com").strip()
FACT_CHECKER_MAX_AGE_DAYS = int(os.environ.get("FACT_CHECKER_MAX_AGE_DAYS", "30"))
FACT_CHECKER_PAGE_SIZE = int(os.environ.get("FACT_CHECKER_PAGE_SIZE", "5"))
FACT_CHECKER_LANGUAGE = os.environ.get("FACT_CHECKER_LANGUAGE", "en-US").strip()
FACT_CHECKER_TIMEOUT = float(os.environ.get("FACT_CHECKER_TIMEOUT", "15"))
FACT_CHECKER_PARALLEL = os.environ.get("FACT_CHECKER_PARALLEL", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
FACT_CHECKER_GOOGLE_URL = os.environ.get("FACT_CHECKER_GOOGLE_URL", "").strip()
FACT_CHECKER_WIKIPEDIA_URL = os.environ.get("FACT_CHECKER_WIKIPEDIA_URL", "").strip()
FACT_CHECKER_WIKIDATA_URL = os.environ.get("FACT_CHECKER_WIKIDATA_URL", "").strip()
FACT_CHECKER_OPENALEX_URL = os.environ.get("FACT_CHECKER_OPENALEX_URL", "").strip().rstrip("/")

# AI pipeline parallelism (CLI / cron / systemd — not Admin HTTP)
PIPELINE_WORKERS = max(1, min(8, int(os.environ.get("PIPELINE_WORKERS", "1"))))
PIPELINE_STALE_MINUTES = max(5, int(os.environ.get("PIPELINE_STALE_MINUTES", "30")))
# Background drain of pending raw_articles while Django is running (see news/pipeline/auto_runner.py).
PIPELINE_AUTO_ENABLED = os.environ.get("PIPELINE_AUTO_ENABLED", "true").strip().lower() in (
    "true",
    "1",
    "yes",
    "on",
)
PIPELINE_AUTO_INTERVAL_SECONDS = max(
    30, int(os.environ.get("PIPELINE_AUTO_INTERVAL_SECONDS", "90"))
)
PIPELINE_AUTO_BATCH_SIZE = max(1, min(500, int(os.environ.get("PIPELINE_AUTO_BATCH_SIZE", "50"))))
PIPELINE_AUTO_MIN_PENDING = max(1, int(os.environ.get("PIPELINE_AUTO_MIN_PENDING", "1")))
PIPELINE_AUTO_LOCK_TTL_SECONDS = max(
    300, int(os.environ.get("PIPELINE_AUTO_LOCK_TTL_SECONDS", "7200"))
)

# BART news summarizer (HF Space preferred; Hub id is fallback when SUMMARIZER_SPACE_ID unset)
SUMMARIZER_MODEL_ID = os.environ.get("SUMMARIZER_MODEL_ID", "").strip() or None
SUMMARIZER_ENABLED = os.environ.get("SUMMARIZER_ENABLED", "true").strip()
SUMMARIZER_MAX_INPUT_CHARS = int(os.environ.get("SUMMARIZER_MAX_INPUT_CHARS", "4000"))
SUMMARIZER_MAX_NEW_TOKENS = int(os.environ.get("SUMMARIZER_MAX_NEW_TOKENS", "128"))

# Many news sites sit behind CDNs that block non-browser user-agents. Use a real
# browser token plus a project suffix so traffic is identifiable.
SCRAPER_USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 TRAK-NewsIngest/1.0",
)
SCRAPER_DELAY_SECONDS = float(os.environ.get("SCRAPER_DELAY_SECONDS", "2.5"))
SCRAPER_REQUEST_TIMEOUT = float(os.environ.get("SCRAPER_REQUEST_TIMEOUT", "30"))
SCRAPER_MAX_HTML_BYTES = int(os.environ.get("SCRAPER_MAX_HTML_BYTES", "5_000_000"))  # 5 MB cap per page

# Keep full page HTML in MongoDB in addition to extracted fields (large; default off).
SCRAPER_STORE_RAW_HTML = os.environ.get("SCRAPER_STORE_RAW_HTML", "false").lower() in (
    "1",
    "true",
    "yes",
)

# Currents API — https://currentsapi.services (free tier ~1000 requests/day).
# One /latest-news call per scrape run (~30 articles). Optional category searches cost +1 req each.
CURRENTS_API_BASE_URL = os.environ.get("CURRENTS_API_BASE_URL", "").strip().rstrip("/")
CURRENTS_API_KEY = os.environ.get("CURRENTS_API_KEY", "").strip()
CURRENTS_API_LANGUAGE = os.environ.get("CURRENTS_API_LANGUAGE", "en").strip() or "en"
CURRENTS_API_COUNTRY = os.environ.get("CURRENTS_API_COUNTRY", "").strip()
CURRENTS_API_SEARCH_CATEGORIES = [
    c.strip()
    for c in os.environ.get("CURRENTS_API_SEARCH_CATEGORIES", "").split(",")
    if c.strip()
]
CURRENTS_API_MAX_REQUESTS_PER_RUN = int(os.environ.get("CURRENTS_API_MAX_REQUESTS_PER_RUN", "5"))
CURRENTS_API_FETCH_ARTICLE_PAGES = os.environ.get(
    "CURRENTS_API_FETCH_ARTICLE_PAGES", "false"
).lower() in ("1", "true", "yes")

# NewsData.io — https://newsdata.io (free tier: 10 articles/request, daily API credits).
NEWSDATA_API_BASE_URL = os.environ.get("NEWSDATA_API_BASE_URL", "").strip().rstrip("/")
NEWSDATA_API_KEY = os.environ.get("NEWSDATA_API_KEY", "").strip()
NEWSDATA_API_LANGUAGE = os.environ.get("NEWSDATA_API_LANGUAGE", "en").strip() or "en"
NEWSDATA_API_COUNTRY = os.environ.get("NEWSDATA_API_COUNTRY", "").strip()
NEWSDATA_API_SIZE = int(os.environ.get("NEWSDATA_API_SIZE", "10"))
NEWSDATA_API_CATEGORIES = [
    c.strip()
    for c in os.environ.get("NEWSDATA_API_CATEGORIES", "").split(",")
    if c.strip()
]
NEWSDATA_API_MAX_REQUESTS_PER_RUN = int(os.environ.get("NEWSDATA_API_MAX_REQUESTS_PER_RUN", "3"))
NEWSDATA_API_FETCH_ARTICLE_PAGES = os.environ.get(
    "NEWSDATA_API_FETCH_ARTICLE_PAGES", "false"
).lower() in ("1", "true", "yes")

# GNews — https://gnews.io (free tier: 10 articles/request, daily request limit).
GNEWS_API_BASE_URL = os.environ.get("GNEWS_API_BASE_URL", "").strip().rstrip("/")
GNEWS_API_KEY = os.environ.get("GNEWS_API_KEY", "").strip()
GNEWS_API_LANGUAGE = os.environ.get("GNEWS_API_LANGUAGE", "en").strip() or "en"
GNEWS_API_COUNTRY = os.environ.get("GNEWS_API_COUNTRY", "").strip()
GNEWS_API_MAX = int(os.environ.get("GNEWS_API_MAX", "10"))
GNEWS_API_CATEGORIES = [
    c.strip().lower()
    for c in os.environ.get("GNEWS_API_CATEGORIES", "").split(",")
    if c.strip()
]
GNEWS_API_MAX_REQUESTS_PER_RUN = int(os.environ.get("GNEWS_API_MAX_REQUESTS_PER_RUN", "4"))
GNEWS_API_FETCH_ARTICLE_PAGES = os.environ.get(
    "GNEWS_API_FETCH_ARTICLE_PAGES", "false"
).lower() in ("1", "true", "yes")

# RSS feeds: merged with `news/scrapers/sources_catalog.py` → RSS_FEED_URLS (add feeds there first).
SCRAPER_RSS_FEED_URLS = [
    u.strip()
    for u in os.environ.get("SCRAPER_RSS_FEED_URLS", "").split(",")
    if u.strip()
]

# Generic CSS-based sites: merged with `sources_catalog.py` → GENERIC_SITES.
SCRAPER_GENERIC_SOURCES = []

# Path to JSON file (list of site configs, or {"sites": [...]}) relative to BASE_DIR if not absolute.
SCRAPER_GENERIC_SOURCES_JSON = os.environ.get("SCRAPER_GENERIC_SOURCES_JSON", "").strip() or None

# Bilingual article TTS (Hugging Face Space — set TTS_API_BASE_URL in .env)
TTS_API_BASE_URL = os.environ.get("TTS_API_BASE_URL", "").strip().rstrip("/") or None
TTS_API_TIMEOUT_SEC = int(os.environ.get("TTS_API_TIMEOUT_SEC", "360") or "360")
# Set TTS_PREFER_LOCAL=true to skip HF Space (runs models on this server; first call is slow).
TTS_PREFER_LOCAL = os.environ.get("TTS_PREFER_LOCAL", "").strip()
# Fast path: Microsoft Edge neural TTS + Google Translate for Urdu (default on).
TTS_USE_EDGE = os.environ.get("TTS_USE_EDGE", "true").strip()
TTS_EDGE_RATE = os.environ.get("TTS_EDGE_RATE", "+12%").strip()

# --- Firebase Cloud Messaging (optional mobile push) ---
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON", "").strip()
GOOGLE_APPLICATION_CREDENTIALS = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

# --- Auth / ops (not secrets; tune per environment) ---
EMAIL_WORKER_THREADS = max(1, int(os.environ.get("EMAIL_WORKER_THREADS", "4")))
OTP_DEV_PREVIEW = os.environ.get("OTP_DEV_PREVIEW", "").lower() in ("1", "true", "yes")
SEED_ADMIN_PASSWORD = os.environ.get("SEED_ADMIN_PASSWORD", "").strip()
SPACY_MODEL = os.environ.get("SPACY_MODEL", "en_core_web_sm").strip() or "en_core_web_sm"
