from django.test import SimpleTestCase

from news.category_matching import interest_matches_hay, user_follows_all_categories
from news.moderation_rules import article_visible_to_users, initial_moderation_status
from news.services.article_query import _doc_haystack


class ModerationVisibilityTests(SimpleTestCase):
    def test_approved_real_visible(self):
        self.assertTrue(
            article_visible_to_users({"moderation_status": "approved", "credibility_label": 0})
        )

    def test_approved_fake_hidden(self):
        self.assertFalse(
            article_visible_to_users({"moderation_status": "approved", "credibility_label": 1})
        )

    def test_approved_suspicious_hidden(self):
        self.assertFalse(
            article_visible_to_users({"moderation_status": "approved", "credibility_label": 2})
        )

    def test_fake_label_not_visible_even_with_real_name(self):
        self.assertFalse(
            article_visible_to_users(
                {
                    "moderation_status": "approved",
                    "credibility_label": 1,
                    "credibility_label_name": "Real",
                }
            )
        )

    def test_suspicious_name_hidden_without_real_code(self):
        self.assertFalse(
            article_visible_to_users(
                {
                    "moderation_status": "approved",
                    "credibility_label_name": "Suspicious",
                }
            )
        )

    def test_review_hidden(self):
        self.assertFalse(article_visible_to_users({"moderation_status": "review", "credibility_label": 0}))

    def test_rejected_hidden(self):
        self.assertFalse(article_visible_to_users({"moderation_status": "rejected", "credibility_label": 0}))

    def test_real_auto_approved(self):
        doc = {"credibility_label": 0}
        self.assertEqual(initial_moderation_status(doc), "approved")
        doc["moderation_status"] = initial_moderation_status(doc)
        self.assertTrue(article_visible_to_users(doc))

    def test_category_keyword_matches_synonym(self):
        doc = {
            "title": "Apple unveils new AI features",
            "summary": "The tech giant announced software updates.",
            "topic_keywords": ["artificial", "smartphones"],
        }
        hay = _doc_haystack(doc)
        self.assertTrue(interest_matches_hay("technology", hay))

    def test_all_categories_selected(self):
        from news.platform_taxonomy import DEFAULT_TAGS_WITH_SUBCATEGORIES

        all_kw = list(DEFAULT_TAGS_WITH_SUBCATEGORIES.keys())
        self.assertTrue(user_follows_all_categories(all_kw))
