"""Check Django, user DB, and MongoDB connectivity."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand

User = get_user_model()


class Command(BaseCommand):
    help = "Run system checks and verify Django users + MongoDB (pymongo)."

    def handle(self, *args, **options):
        self.stdout.write("=== Django system check ===")
        call_command("check", verbosity=1)

        self.stdout.write("\n=== Users (djongo / AUTH_USER_MODEL) ===")
        try:
            total = User.objects.count()
            admins = list(
                User.objects.filter(role=User.Role.ADMIN).values_list("email", flat=True)
            )
            users = list(
                User.objects.filter(role=User.Role.USER).values_list("email", flat=True)
            )
            self.stdout.write(self.style.SUCCESS(f"Total users: {total}"))
            self.stdout.write(f"Admins ({len(admins)}): {', '.join(admins) or '(none)'}")
            self.stdout.write(f"Regular users ({len(users)}): {len(users)} account(s)")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"User query failed: {e}"))

        self.stdout.write("\n=== MongoDB (articles / keywords) ===")
        uri = getattr(settings, "MONGODB_URI", "")
        dbname = getattr(settings, "MONGODB_RAW_DATABASE", "")
        self.stdout.write(f"URI host (masked): {uri.split('@')[-1] if '@' in uri else uri}")
        self.stdout.write(f"Database: {dbname}")
        try:
            from news.mongo_db import get_client, get_db

            get_client().admin.command("ping")
            db = get_db()
            raw_n = db[settings.MONGODB_RAW_COLLECTION].estimated_document_count()
            proc_n = db[settings.MONGODB_PROCESSED_COLLECTION].estimated_document_count()
            kw_n = db[settings.MONGODB_USER_KEYWORDS_COLLECTION].estimated_document_count()
            self.stdout.write(self.style.SUCCESS("Ping: OK"))
            self.stdout.write(
                f"Collections — raw: {raw_n}, processed: {proc_n}, user_keywords: {kw_n}"
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"MongoDB error: {e}"))

        self.stdout.write("\n=== ADMIN_EMAILS (registration → admin role) ===")
        self.stdout.write(getattr(settings, "ADMIN_EMAILS", "") or "(empty)")

        self.stdout.write(self.style.SUCCESS("\nDiagnostics finished."))
