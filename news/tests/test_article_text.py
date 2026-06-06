from django.test import SimpleTestCase

from news.article_text import build_card_summary


class BuildCardSummaryTests(SimpleTestCase):
    def test_uses_stored_summary_when_valid(self):
        text = build_card_summary(
            title="Headline",
            stored_summary="A concise summary of the story.",
            body="Full article body that is much longer.",
        )
        self.assertEqual(text, "A concise summary of the story.")

    def test_falls_back_to_body_when_summary_repeats_title(self):
        title = "Pakistan wins cricket match"
        body = (
            f"{title} against India in a thrilling finale on Sunday. "
            "The team celebrated after the final wicket."
        )
        text = build_card_summary(
            title=title,
            stored_summary=title,
            body=body,
        )
        self.assertTrue(text)
        self.assertNotEqual(text.lower(), title.lower())

    def test_falls_back_to_body_snippet_when_summary_missing(self):
        body = (
            "Scientists discovered a new species in the northern region. "
            "Researchers say the finding could change how we understand migration."
        )
        text = build_card_summary(title="New species found", stored_summary="", body=body)
        self.assertIn("Scientists discovered", text)

    def test_returns_empty_without_body_or_summary(self):
        self.assertEqual(build_card_summary(title="Only a title", stored_summary="", body=""), "")
