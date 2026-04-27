from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False)
class AdminApiValidationTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(email="admin@test.com", password="StrongPass123!", role="admin")
        self.user = User.objects.create_user(email="user@test.com", password="StrongPass123!", role="user")
        self.client.force_authenticate(self.admin)

    def test_patch_user_rejects_invalid_boolean(self):
        res = self.client.patch(
            f"/api/admin/users/{self.user.pk}/",
            {"is_active": "not-a-bool"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_cannot_delete_self(self):
        res = self.client.delete(f"/api/admin/users/{self.admin.pk}/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
