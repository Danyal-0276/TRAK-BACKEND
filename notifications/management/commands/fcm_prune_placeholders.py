"""Remove placeholder device tokens that cannot receive FCM push."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from news.mongo_db import device_tokens_collection
from notifications.fcm import _is_deliverable_fcm_token


class Command(BaseCommand):
    help = "Delete trak-web-* and trak-mobile-* placeholder tokens from MongoDB."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report counts without deleting.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        coll = device_tokens_collection()
        to_delete = []
        deliverable = 0
        for doc in coll.find({}, {"token": 1}):
            token = str(doc.get("token") or "")
            if _is_deliverable_fcm_token(token):
                deliverable += 1
            elif token.startswith("trak-web-") or token.startswith("trak-mobile-"):
                to_delete.append(doc["_id"])

        self.stdout.write(f"Deliverable FCM tokens (kept): {deliverable}")
        self.stdout.write(f"Placeholder tokens ({'would delete' if dry_run else 'deleting'}): {len(to_delete)}")

        if dry_run or not to_delete:
            return

        result = coll.delete_many({"_id": {"$in": to_delete}})
        self.stdout.write(self.style.SUCCESS(f"Deleted {result.deleted_count} placeholder token(s)."))
