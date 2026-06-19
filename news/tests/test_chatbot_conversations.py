from django.test import SimpleTestCase

from news.chatbot.conversations import _sessions_from_legacy_messages


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
