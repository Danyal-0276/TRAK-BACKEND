"""Create or update the three default admin accounts (same password)."""

from __future__ import annotations

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Create or reset the three built-in admins (danyal/shahroz/abdullah @ admin.com). "
        "Password from SEED_ADMIN_PASSWORD or --password. Does not create extra ADMIN_EMAILS."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            type=str,
            default="",
            help="Override SEED_ADMIN_PASSWORD for this run only.",
        )
        parser.add_argument(
            "--database",
            type=str,
            default=DEFAULT_DB_ALIAS,
            help="Database alias (default: default).",
        )

    def handle(self, *args, **options):
        pwd = (options.get("password") or "").strip() or os.environ.get(
            "SEED_ADMIN_PASSWORD", ""
        ).strip()
        if not pwd:
            self.stderr.write(
                self.style.ERROR(
                    "Set SEED_ADMIN_PASSWORD in the environment, or pass --password."
                )
            )
            return

        using = options["database"]
        emails = list(getattr(settings, "BUILTIN_ADMIN_EMAILS_LIST", []) or [])
        if len(emails) != 3:
            self.stderr.write(self.style.ERROR("BUILTIN_ADMIN_EMAILS_LIST not configured."))
            return

        created, updated = 0, 0
        for email in emails:
            user = User.objects.using(using).filter(email=email).first()
            if user is None:
                User.objects.db_manager(using).create_user(
                    email,
                    pwd,
                    role=User.Role.ADMIN,
                    is_staff=True,
                )
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created admin: {email}"))
            else:
                dirty = False
                if user.role != User.Role.ADMIN:
                    user.role = User.Role.ADMIN
                    dirty = True
                if not user.is_staff:
                    user.is_staff = True
                    dirty = True
                user.set_password(pwd)
                dirty = True
                if dirty:
                    user.save(using=using)
                updated += 1
                self.stdout.write(self.style.WARNING(f"Updated admin: {email}"))

        self.stdout.write(
            self.style.SUCCESS(f"Done. created={created}, password_reset/updated={updated}")
        )
