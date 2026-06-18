from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from news.mongo_db import notifications_collection
from notifications.fcm import _is_deliverable_fcm_token, send_fcm_to_user
from notifications.user_scope import user_notifications_query

User = get_user_model()

REAL_FCM_TOKEN = "f" * 142


class FcmTokenValidationTests(TestCase):
    def test_accepts_realistic_fcm_token(self):
        self.assertTrue(_is_deliverable_fcm_token(REAL_FCM_TOKEN))

    def test_rejects_short_tokens(self):
        self.assertFalse(_is_deliverable_fcm_token("abc"))

    def test_rejects_placeholder_tokens(self):
        self.assertFalse(_is_deliverable_fcm_token("trak-web-123456789012345678901234567890"))
        self.assertFalse(_is_deliverable_fcm_token("trak-mobile-123456789012345678901234567890"))


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class DeviceTokenRegisterTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="fcm@test.com", password="StrongPass123!", role="user"
        )
        self.client.force_authenticate(self.user)

    def test_rejects_fake_mobile_token(self):
        res = self.client.post(
            "/api/notifications/device-token/",
            {"token": "trak-mobile-fake", "platform": "mobile"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_accepts_real_mobile_token(self):
        res = self.client.post(
            "/api/notifications/device-token/",
            {"token": REAL_FCM_TOKEN, "platform": "mobile"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_rejects_fake_web_token(self):
        res = self.client.post(
            "/api/notifications/device-token/",
            {"token": "trak-web-fake-token-placeholder", "platform": "web"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)


class FcmSendTests(TestCase):
    @patch("notifications.fcm._ensure_app")
    def test_skips_when_fcm_disabled(self, mock_ensure):
        mock_ensure.return_value = None
        stats = send_fcm_to_user(1, "Title", "Body")
        self.assertEqual(stats, {"attempted": 0, "success": 0, "failure": 0})

    @patch("notifications.fcm._ensure_app")
    @patch("news.mongo_db.device_tokens_collection")
    @patch("firebase_admin.messaging.send_each_for_multicast")
    def test_filters_placeholder_tokens(self, mock_send_fn, mock_coll_fn, mock_ensure):
        mock_ensure.return_value = object()
        coll = MagicMock()
        mock_coll_fn.return_value = coll
        coll.find.return_value = [
            {"token": "trak-mobile-placeholder-not-real", "platform": "mobile"},
            {"token": REAL_FCM_TOKEN, "platform": "mobile"},
        ]
        mock_result = MagicMock()
        mock_result.responses = [MagicMock(success=True, exception=None)]
        mock_send_fn.return_value = mock_result

        stats = send_fcm_to_user(42, "Hello", "World", data={"type": "system"})

        self.assertEqual(stats["attempted"], 1)
        self.assertEqual(stats["success"], 1)
        sent_tokens = mock_send_fn.call_args.args[0].tokens
        self.assertEqual(sent_tokens, [REAL_FCM_TOKEN])

    @patch("notifications.fcm._ensure_app")
    @patch("news.mongo_db.device_tokens_collection")
    @patch("firebase_admin.messaging.send_each_for_multicast")
    def test_skips_non_mobile_platform_tokens(self, mock_send_fn, mock_coll_fn, mock_ensure):
        mock_ensure.return_value = object()
        coll = MagicMock()
        mock_coll_fn.return_value = coll
        coll.find.return_value = [
            {"token": REAL_FCM_TOKEN, "platform": "web"},
            {"token": REAL_FCM_TOKEN, "platform": "unknown"},
        ]

        stats = send_fcm_to_user(42, "Hello", "World")

        self.assertEqual(stats, {"attempted": 0, "success": 0, "failure": 0})
        mock_send_fn.assert_not_called()


class NotificationChannelTests(TestCase):
    @patch("notifications.delivery.user_preferences_collection")
    def test_keyword_match_never_emails(self, mock_prefs_fn):
        from notifications.delivery import _channels_for_user

        coll = MagicMock()
        mock_prefs_fn.return_value = coll
        coll.find_one.return_value = {
            "email_enabled": True,
            "push_enabled": True,
            "keyword_alerts": True,
        }
        channels = _channels_for_user(1, audience="user", ntype="keyword_match")
        self.assertFalse(channels["email"])
        self.assertTrue(channels["in_app"])
        self.assertTrue(channels["push"])

    @patch("notifications.delivery.user_preferences_collection")
    def test_non_keyword_user_alerts_respect_email_pref(self, mock_prefs_fn):
        from notifications.delivery import _channels_for_user

        coll = MagicMock()
        mock_prefs_fn.return_value = coll
        coll.find_one.return_value = {"email_enabled": True, "push_enabled": True}
        channels = _channels_for_user(1, audience="user", ntype="welcome_back")
        self.assertTrue(channels["email"])
        self.assertTrue(channels["in_app"])

        coll.find_one.return_value = {"email_enabled": False, "push_enabled": True}
        channels = _channels_for_user(1, audience="user", ntype="welcome_back")
        self.assertFalse(channels["email"])


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
        joined = self.user.created_at.astimezone(timezone.utc)
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
        joined = self.user.created_at.astimezone(timezone.utc)
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
