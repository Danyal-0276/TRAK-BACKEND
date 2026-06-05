from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from news.mongo_db import notifications_collection
from notifications.user_scope import user_notifications_query

User = get_user_model()


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class NotificationPreferenceValidationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="notif@test.com", password="StrongPass123!", role="user")
        self.client.force_authenticate(self.user)

    def test_preferences_reject_invalid_boolean(self):
        res = self.client.patch(
            "/api/notifications/preferences/",
            {"push_enabled": "invalid"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class UserNotificationScopeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="scope@test.com", password="StrongPass123!", role="user"
        )
        self.client.force_authenticate(self.user)
        self.col = notifications_collection()

    def test_list_hides_notifications_before_account_creation(self):
        joined = self.user.date_joined.astimezone(timezone.utc)
        old = joined - timedelta(hours=2)
        new = joined + timedelta(minutes=5)
        self.col.insert_many(
            [
                {
                    "user_id": self.user.pk,
                    "audience": "user",
                    "type": "keyword_match",
                    "text": "old alert",
                    "read": False,
                    "created_at": old,
                    "updated_at": old,
                },
                {
                    "user_id": self.user.pk,
                    "audience": "user",
                    "type": "keyword_match",
                    "text": "new alert",
                    "read": False,
                    "created_at": new,
                    "updated_at": new,
                },
            ]
        )
        res = self.client.get("/api/notifications/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        texts = [r["text"] for r in res.data["results"]]
        self.assertIn("new alert", texts)
        self.assertNotIn("old alert", texts)

    def test_unread_count_respects_account_creation(self):
        joined = self.user.date_joined.astimezone(timezone.utc)
        self.col.insert_one(
            {
                "user_id": self.user.pk,
                "audience": "user",
                "type": "system",
                "text": "pre-signup",
                "read": False,
                "created_at": joined - timedelta(days=1),
                "updated_at": joined - timedelta(days=1),
            }
        )
        res = self.client.get("/api/notifications/unread-count/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["unread"], 0)
        q = user_notifications_query(self.user)
        self.assertIn("created_at", q)
