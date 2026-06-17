"""Send a test FCM push to a user by email (for verifying mobile delivery)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from news.mongo_db import device_tokens_collection
from notifications.fcm import _ensure_app, _is_deliverable_fcm_token, send_fcm_to_user

User = get_user_model()


class Command(BaseCommand):
    help = "Send a test FCM notification to all deliverable tokens for a user."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="User email address")
        parser.add_argument("--title", default="TRAK test push", help="Notification title")
        parser.add_argument(
            "--body",
            default="If you see this, Firebase FCM is working correctly.",
            help="Notification body",
        )

    def handle(self, *args, **options):
        email = str(options["email"]).strip().lower()
        title = str(options["title"])
        body = str(options["body"])

        if _ensure_app() is None:
            raise CommandError(
                "FCM is not configured. Set GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_CREDENTIALS_JSON."
            )

        user = User.objects.filter(email__iexact=email).first()
        if not user:
            raise CommandError(f"No user found with email: {email}")

        coll = device_tokens_collection()
        tokens = []
        for doc in coll.find({"user_id": user.pk}, {"token": 1, "platform": 1}):
            token = str(doc.get("token") or "")
            if _is_deliverable_fcm_token(token):
                tokens.append((token[:12] + "...", doc.get("platform", "unknown")))

        self.stdout.write(f"User: {user.email} (id={user.pk})")
        self.stdout.write(f"Deliverable FCM tokens: {len(tokens)}")
        for preview, platform in tokens:
            self.stdout.write(f"  - {preview} ({platform})")

        if not tokens:
            raise CommandError(
                "No deliverable FCM tokens for this user. "
                "Open the native TRAK app on a device, log in, and allow notifications."
            )

        stats = send_fcm_to_user(
            user.pk,
            title=title,
            body=body,
            data={"type": "system", "text": body},
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"FCM send complete: {stats['success']}/{stats['attempted']} succeeded, "
                f"{stats['failure']} failed."
            )
        )
        if stats["success"] == 0:
            raise CommandError("FCM send failed for all tokens. Check server logs for details.")
