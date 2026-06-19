from unittest.mock import patch

from django.test import SimpleTestCase

from bson import ObjectId

from news.chatbot.conversations import _sessions_from_legacy_messages, _user_id_match, _user_id_variants, migrate_legacy_history


class ChatbotConversationSplitTests(SimpleTestCase):
    def test_splits_legacy_thread_per_user_turn(self):
        messages = [
            {"role": "user", "text": "First question"},
            {"role": "bot", "text": "First answer"},
            {"role": "user", "text": "Second question"},
            {"role": "bot", "text": "Second answer"},
            {"role": "user", "text": "Third question"},
            {"role": "bot", "text": "Third answer"},
        ]
        sessions = _sessions_from_legacy_messages(messages)
        self.assertEqual(len(sessions), 3)
        self.assertEqual(sessions[0][0]["text"], "First question")
        self.assertEqual(sessions[1][0]["text"], "Second question")
        self.assertEqual(len(sessions[0]), 2)
        self.assertEqual(len(sessions[2]), 2)

    def test_single_exchange_stays_one_session(self):
        messages = [
            {"role": "user", "text": "Hello"},
            {"role": "bot", "text": "Hi"},
        ]
        sessions = _sessions_from_legacy_messages(messages)
        self.assertEqual(len(sessions), 1)

    def test_resplit_splits_two_exchange_legacy_blob(self):
        from unittest.mock import MagicMock, patch

        messages = [
            {"role": "user", "text": "First question"},
            {"role": "bot", "text": "First answer"},
            {"role": "user", "text": "Second question"},
            {"role": "bot", "text": "Second answer"},
        ]
        row = {"_id": "abc", "messages": messages, "legacy_import": True}
        col = MagicMock()
        col.find.return_value = [row]
        with patch("news.chatbot.conversations.chatbot_conversations_collection", return_value=col), patch(
            "news.chatbot.conversations._insert_legacy_sessions"
        ) as insert:
            from news.chatbot.conversations import resplit_legacy_import_if_needed

            resplit_legacy_import_if_needed(42)
        col.delete_one.assert_called_once_with({"_id": "abc"})
        insert.assert_called_once_with(42, messages, legacy_split=True)

    def test_user_id_variants_support_objectid(self):
        oid = ObjectId()
        variants = _user_id_variants(oid)
        self.assertIn(oid, variants)
        self.assertIn(str(oid), variants)
        match = _user_id_match(oid)
        self.assertIn(oid, match["user_id"]["$in"])

    def test_migrate_legacy_history_accepts_objectid(self):
        oid = ObjectId()
        with patch("news.chatbot.conversations.chatbot_history_collection") as hist_col, patch(
            "news.chatbot.conversations._insert_legacy_sessions"
        ) as insert:
            hist_col.return_value.find_one.return_value = {"messages": []}
            migrate_legacy_history(oid)
        hist_col.return_value.find_one.assert_called()
        insert.assert_not_called()
