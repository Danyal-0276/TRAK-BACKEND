from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

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
