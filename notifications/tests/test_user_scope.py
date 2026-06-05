from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from notifications.user_scope import (
    effective_lookback_since,
    user_account_started_at,
)


class UserScopeTests(SimpleTestCase):
    def test_account_started_uses_created_at(self):
        created = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        user = SimpleNamespace(pk=7, created_at=created)
        self.assertEqual(user_account_started_at(user), created)

    def test_effective_lookback_not_before_signup(self):
        created = datetime.now(timezone.utc)
        user = SimpleNamespace(pk=7, created_at=created)
        since = effective_lookback_since(user, hours=168)
        self.assertGreaterEqual(since, created)

    @patch("notifications.user_scope.user_keywords_collection")
    def test_effective_lookback_respects_interests_save(self, mock_col):
        account = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
        interests = datetime(2026, 6, 3, 10, 0, tzinfo=timezone.utc)
        user = SimpleNamespace(pk=7, created_at=account)
        mock_col.return_value.find_one.return_value = {"created_at": interests}
        since = effective_lookback_since(user, hours=168)
        self.assertEqual(since, interests)
