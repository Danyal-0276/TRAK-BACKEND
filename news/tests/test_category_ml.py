from django.test import SimpleTestCase, override_settings

from news.categorization.matching import (
    article_browse_slugs,
    article_matches_category,
    interest_matches_article,
)


class CategoryMlMatchingTests(SimpleTestCase):
    def test_ml_category_match_all_labels(self):
        doc = {
            "title": "Fed raises interest rates",
            "primary_category": "finance",
            "categories": ["finance", "business"],
            "category_scores": {"finance": 0.58, "business": 0.46},
        }
        self.assertTrue(article_matches_category(doc, "finance"))
        self.assertFalse(article_matches_category(doc, "business"))

    @override_settings(CATEGORY_BROWSE_PRIMARY_ONLY=False)
    def test_secondary_browse_when_multi_label_enabled(self):
        doc = {
            "title": "Fed raises interest rates",
            "primary_category": "finance",
            "categories": ["finance", "business"],
            "category_scores": {"finance": 0.58, "business": 0.46},
        }
        self.assertTrue(article_matches_category(doc, "finance"))
        self.assertTrue(article_matches_category(doc, "business"))
        self.assertFalse(article_matches_category(doc, "sports"))

    @override_settings(CATEGORY_RULE_FALLBACK_ENABLED=False)
    def test_ml_only_no_legacy(self):
        doc = {
            "title": "Apple unveils new AI features",
            "summary": "The tech giant announced software updates.",
            "primary_category": "technology",
            "categories": ["technology"],
        }
        self.assertTrue(article_matches_category(doc, "technology"))
        self.assertFalse(article_matches_category(doc, "politics"))

    @override_settings(CATEGORY_BROWSE_PRIMARY_ONLY=False)
    def test_secondary_label_matches_when_in_categories(self):
        doc = {
            "title": "ICT exports hit $3.4b",
            "primary_category": "finance",
            "categories": ["finance", "technology"],
            "category_scores": {"finance": 0.55, "technology": 0.46},
        }
        self.assertTrue(article_matches_category(doc, "finance"))
        self.assertTrue(article_matches_category(doc, "technology"))

    def test_weak_secondary_excluded_by_score(self):
        doc = {
            "title": "Football player news",
            "primary_category": "sports",
            "categories": ["sports", "technology"],
            "category_scores": {"sports": 0.62, "technology": 0.31},
        }
        self.assertTrue(article_matches_category(doc, "sports"))
        self.assertFalse(article_matches_category(doc, "technology"))

    def test_ml_labeled_rejects_unlisted_category(self):
        doc = {
            "title": "Thomas Partey denied entry into Canada",
            "summary": "Football player misses World Cup opener.",
            "primary_category": "sports",
            "categories": ["sports"],
        }
        self.assertFalse(article_matches_category(doc, "technology"))
        self.assertTrue(article_matches_category(doc, "sports"))

    @override_settings(CATEGORY_BROWSE_PRIMARY_ONLY=True)
    def test_default_browse_uses_primary_only(self):
        doc = {
            "primary_category": "finance",
            "categories": ["finance", "business"],
            "category_scores": {"finance": 0.62, "business": 0.50},
        }
        self.assertEqual(article_browse_slugs(doc), {"finance"})
        self.assertTrue(article_matches_category(doc, "finance"))
        self.assertFalse(article_matches_category(doc, "business"))

    @override_settings(CATEGORY_BROWSE_PRIMARY_ONLY=False)
    def test_secondary_without_scores_not_promoted(self):
        doc = {
            "primary_category": "sports",
            "categories": ["sports", "technology", "business"],
        }
        self.assertEqual(article_browse_slugs(doc), {"sports"})

    def test_primary_only_mode(self):
        doc = {
            "primary_category": "finance",
            "categories": ["finance", "business"],
        }
        self.assertTrue(article_matches_category(doc, "finance"))
        self.assertFalse(article_matches_category(doc, "business"))
        self.assertEqual(article_browse_slugs(doc), {"finance"})

    def test_keyword_matches_ml_category(self):
        doc = {
            "title": "Premier League results",
            "primary_category": "sports",
            "categories": ["sports"],
        }
        self.assertTrue(interest_matches_article(doc, "sports"))

    @override_settings(KEYWORD_EMBEDDING_ENABLED=False, CATEGORY_RULE_FALLBACK_ENABLED=False)
    def test_custom_keyword_without_embedding_no_match(self):
        doc = {
            "title": "Tesla reports record deliveries",
            "primary_category": "automotive",
            "categories": ["automotive", "business"],
        }
        self.assertFalse(interest_matches_article(doc, "Tesla"))

    def test_legacy_fallback_when_no_ml_fields(self):
        doc = {
            "title": "Apple unveils new AI features",
            "summary": "The tech giant announced software updates.",
            "topic_keywords": ["artificial", "smartphones"],
        }
        self.assertTrue(article_matches_category(doc, "technology"))
