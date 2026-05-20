# Auth security (email validation, OTP, rate limits)

## Install

```bash
pip install -r requirements.txt
python manage.py migrate accounts
```

## New API endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/email/validate/` | Public | Format + MX + disposable check |
| POST | `/api/auth/email-verification/send/` | JWT | Send 6-digit OTP to user email |
| POST | `/api/auth/email-verification/verify/` | JWT | `{ "code": "123456" }` → `email_verified=true` |
| POST | `/api/auth/email-verification/resend/` | JWT | Resend (60s cooldown, invalidates old OTP) |

Registration still returns JWT immediately and sends a verification OTP when `REGISTER_SEND_VERIFICATION_OTP=true` (default).

## Environment (`.env`)

```env
# SMTP (required for real email delivery)
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=your@gmail.com
EMAIL_HOST_PASSWORD=app-password
DEFAULT_FROM_EMAIL=TRAK <noreply@yourdomain.com>

# OTP
OTP_EXPIRY_SECONDS=300
OTP_RESEND_COOLDOWN_SECONDS=60
OTP_MAX_ATTEMPTS=5
OTP_HASH_SECRET=change-me-in-production

# Email validation
EMAIL_VALIDATION_CHECK_MX=true
EMAIL_VALIDATION_BLOCK_DISPOSABLE=true

# Brute force
AUTH_LOGIN_MAX_ATTEMPTS=10
AUTH_LOGIN_LOCKOUT_SECONDS=900
AUTH_OTP_VERIFY_MAX_ATTEMPTS=5

# Throttles (DRF + django-ratelimit on views)
THROTTLE_OTP_SEND=10/hour
THROTTLE_OTP_VERIFY=30/hour
THROTTLE_EMAIL_VALIDATE=60/hour

# Shared cache for OTP across workers (recommended in production)
REDIS_URL=redis://127.0.0.1:6379/0
```

## Architecture

- `accounts/services/email_validation.py` — email-validator + MX + disposable list
- `accounts/services/otp_service.py` — hashed OTP in `EmailOtp` model
- `accounts/services/email_service.py` — HTML/text templates
- `accounts/services/security.py` — login/OTP brute-force counters (cache)
- `accounts/decorators.py` — django-ratelimit mixin for API views
