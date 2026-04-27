from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .serializers import RegisterSerializer

User = get_user_model()


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class RegisterSerializerTests(APITestCase):
    def test_register_serializer_never_assigns_admin_role(self):
        payload = {
            "email": "danyal@admin.com",
            "password": "StrongPass123!",
            "password_confirm": "StrongPass123!",
        }
        serializer = RegisterSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()
        self.assertEqual(user.role, "user")

    @override_settings(DEBUG=False, ALLOW_DEMO_SOCIAL_LOGIN=False)
    def test_social_demo_login_is_not_available(self):
        payload = {"provider": "google", "email": "demo@example.com"}
        res = self.client.post("/api/auth/social/demo-login/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_login_view_still_works_for_regular_user(self):
        User.objects.create_user(email="release_user@example.com", password="StrongPass123!", role="user")
        login = self.client.post(
            "/api/auth/login/",
            {"email": "release_user@example.com", "password": "StrongPass123!"},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.assertIn("access", login.data)
