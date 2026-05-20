from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """Email-based user with admin/user role (server-assigned on registration)."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        USER = "user", "User"

    email = models.EmailField("email address", unique=True)
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.USER,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_super_admin = models.BooleanField(
        default=False,
        help_text="Can create/delete admins and change user roles.",
    )
    email_verified = models.BooleanField(
        default=False,
        help_text="Set after successful email OTP verification.",
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"

    def __str__(self) -> str:
        return self.email


class EmailOtp(models.Model):
    """Hashed OTP records for email verification, login, and password reset."""

    class Purpose(models.TextChoices):
        EMAIL_VERIFICATION = "email_verification", "Email verification"
        LOGIN = "login", "Login"
        PASSWORD_RESET = "password_reset", "Password reset"
        CONTACT_VERIFY = "contact_verify", "Contact verify"

    email = models.EmailField(db_index=True)
    purpose = models.CharField(max_length=32, choices=Purpose.choices, db_index=True)
    code_hash = models.CharField(max_length=64)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="email_otps",
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    is_used = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "accounts_email_otp"
        indexes = [
            models.Index(fields=["email", "purpose", "is_used"]),
        ]

    def __str__(self) -> str:
        return f"{self.email} [{self.purpose}]"
