from django.db import migrations, models
import django_mongodb_backend.fields


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_user_is_super_admin"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="email_verified",
            field=models.BooleanField(
                default=False,
                help_text="Set after successful email OTP verification.",
            ),
        ),
        migrations.CreateModel(
            name="EmailOtp",
            fields=[
                (
                    "id",
                    django_mongodb_backend.fields.ObjectIdAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("email", models.EmailField(db_index=True, max_length=254)),
                (
                    "purpose",
                    models.CharField(
                        choices=[
                            ("email_verification", "Email verification"),
                            ("login", "Login"),
                            ("password_reset", "Password reset"),
                            ("contact_verify", "Contact verify"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                ("code_hash", models.CharField(max_length=64)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("max_attempts", models.PositiveSmallIntegerField(default=5)),
                ("is_used", models.BooleanField(db_index=True, default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.CASCADE,
                        related_name="email_otps",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "db_table": "accounts_email_otp",
                "indexes": [
                    models.Index(
                        fields=["email", "purpose", "is_used"],
                        name="accounts_em_email_p_8a1f2d_idx",
                    ),
                ],
            },
        ),
    ]
