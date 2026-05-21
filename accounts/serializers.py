from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User
from .validators import validate_email_address


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, min_length=8)
    id = serializers.CharField(read_only=True)
    email = serializers.EmailField(validators=[validate_email_address])

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "password",
            "password_confirm",
            "role",
            "email_verified",
            "created_at",
        )
        read_only_fields = ("id", "role", "email_verified", "created_at")

    def validate_email(self, value):
        return validate_email_address(value)

    def validate(self, attrs):
        password = attrs["password"].strip()
        password_confirm = attrs["password_confirm"].strip()
        if password != password_confirm:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        validate_password(password)
        attrs["password"] = password
        attrs["password_confirm"] = password_confirm
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        password = validated_data.pop("password").strip()
        email = validated_data.pop("email").strip().lower()
        # Self-registration never grants admin privileges.
        role = str(User.Role.USER)
        user = User(email=email, role=role)
        user.set_password(password)
        user.save()
        return user


class UserSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "role",
            "is_super_admin",
            "email_verified",
            "created_at",
        )
        read_only_fields = fields


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        # Be tolerant of mobile keyboard/autofill artifacts.
        username_field = self.username_field
        raw_email = attrs.get(username_field, "")
        if isinstance(raw_email, str):
            attrs[username_field] = validate_email_address(raw_email.strip())
        raw_password = attrs.get("password")
        password_candidates = [raw_password]
        if isinstance(raw_password, str):
            stripped = raw_password.strip()
            if stripped != raw_password:
                password_candidates.append(stripped)

        last_error = None
        data = None
        for candidate in password_candidates:
            attempt = attrs.copy()
            attempt["password"] = candidate
            try:
                data = super().validate(attempt)
                break
            except Exception as exc:  # pragma: no cover - serializer-level fallback
                last_error = exc

        if data is None and last_error is not None:
            raise last_error

        data["user"] = UserSerializer(self.user).data
        return data


class PasswordResetRequestSerializer(serializers.Serializer):
    """Same shape as EmailValidateSerializer (single validate_email hook)."""

    email = serializers.EmailField()

    def validate_email(self, value):
        return validate_email_address(value)


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        validate_password(attrs["password"])
        return attrs


class PasswordResetOtpVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return validate_email_address(value)

    code = serializers.CharField(min_length=6, max_length=6)

    def validate_code(self, value):
        code = (value or "").strip()
        if not code.isdigit() or len(code) != 6:
            raise serializers.ValidationError("Enter a valid 6-digit code.")
        return code


class PasswordResetOtpConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return validate_email_address(value)

    code = serializers.CharField(
        min_length=6, max_length=6, required=False, allow_blank=True
    )
    reset_token = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, min_length=8)

    def validate_code(self, value):
        code = (value or "").strip()
        if not code:
            return ""
        if not code.isdigit() or len(code) != 6:
            raise serializers.ValidationError("Enter a valid 6-digit code.")
        return code

    def validate_reset_token(self, value):
        return (value or "").strip()

    def validate(self, attrs):
        code = attrs.get("code") or ""
        reset_token = attrs.get("reset_token") or ""
        if not code and not reset_token:
            raise serializers.ValidationError(
                {"detail": "Reset session expired. Verify your code again."}
            )
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        validate_password(attrs["password"])
        return attrs


class EmailValidateSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return validate_email_address(value)


class EmailVerificationSendSerializer(serializers.Serializer):
    pass


class EmailVerificationVerifySerializer(serializers.Serializer):
    code = serializers.CharField(min_length=6, max_length=6)

    def validate_code(self, value):
        code = (value or "").strip()
        if not code.isdigit() or len(code) != 6:
            raise serializers.ValidationError("Enter a valid 6-digit code.")
        return code


class EmailVerificationResendSerializer(serializers.Serializer):
    pass
