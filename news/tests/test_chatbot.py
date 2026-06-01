from django.test import SimpleTestCase

from news.chatbot.intents import (
    build_search_query,
    classify_empty_result,
    detect_intent,
    extract_search_terms,
    has_news_intent,
    should_link_article,
)
from news.chatbot.gemini_chat import (
    _normalize_text,
    build_local_summary_paragraph,
    finalize_reply_with_article_cards,
    finalize_summarize_reply,
    format_related_articles_intro,
    pick_primary_article,
    sanitize_bot_reply,
)


class ChatbotIntentTests(SimpleTestCase):
    def test_identity_intent(self):
        self.assertEqual(detect_intent("Who built TRAK AI?"), "identity")
        self.assertEqual(detect_intent("Who made you?"), "identity")

    def test_headlines_intent(self):
        self.assertEqual(detect_intent("Top tech headlines today"), "headlines")

    def test_summarize_intent(self):
        self.assertEqual(detect_intent("Summarize Pakistan news"), "summarize")
        self.assertEqual(detect_intent("Give me a summary of tech headlines"), "summarize")

    def test_off_topic_intent(self):
        self.assertEqual(detect_intent("Write me a python function"), "off_topic")
        self.assertEqual(detect_intent("Tell me a joke"), "off_topic")
        self.assertEqual(detect_intent("Hello"), "off_topic")
        self.assertEqual(detect_intent("write me an essay"), "off_topic")
        self.assertEqual(detect_intent("write me an eassy"), "off_topic")
        self.assertFalse(has_news_intent("write me an essay"))

    def test_classify_empty_non_news(self):
        self.assertEqual(classify_empty_result("write python code"), "off_topic")

    def test_classify_empty_news(self):
        self.assertEqual(classify_empty_result("election results pakistan"), "no_match")

    def test_has_news_intent(self):
        self.assertTrue(has_news_intent("Pakistan economy news"))
        self.assertFalse(has_news_intent("hello there"))

    def test_news_stays_search(self):
        self.assertEqual(detect_intent("What happened in Pakistan today?"), "search")

    def test_search_intent(self):
        self.assertEqual(detect_intent("What happened in Pakistan elections?"), "search")

    def test_extract_terms(self):
        terms = extract_search_terms("Summarize Pakistan news")
        self.assertIn("pakistan", terms)

    def test_build_search_query(self):
        q = build_search_query("Tell me about Apple earnings report")
        self.assertIn("apple", q)
        self.assertIn("earnings", q)

    def test_should_link(self):
        art = {"title": "Pakistan economy grows amid reforms", "summary": "Islamabad..."}
        self.assertTrue(should_link_article("Pakistan economy news", art))
        self.assertFalse(should_link_article("weather in mars", art))

    def test_pick_primary(self):
        articles = [
            {"id": "1", "title": "Tech stocks rally", "summary": "Markets up"},
            {"id": "2", "title": "Pakistan flood relief", "summary": "Aid efforts"},
        ]
        primary = pick_primary_article("Pakistan floods", articles)
        self.assertEqual(primary["id"], "2")

    def test_sanitize_urls(self):
        text = sanitize_bot_reply("See https://evil.com for more.")
        self.assertNotIn("http", text)

    def test_related_intro_single(self):
        intro = format_related_articles_intro(1)
        self.assertIn("article I found", intro.lower())
        self.assertNotIn("We have this in TRAK", intro)

    def test_finalize_strips_title_dump(self):
        title = "Stop writing useEffect for data fetching"
        linkable = [{"id": "1", "title": title, "source": "dev_to"}]
        bloated = (
            f"We have this in TRAK: {title} (dev_to). Tap the article card below. {title}. "
            "Alova transforms data-fetching..."
        )
        out = finalize_reply_with_article_cards(bloated, linkable)
        self.assertNotIn("We have this in TRAK", out)
        self.assertNotIn(title, out)
        self.assertIn("article I found", out.lower())

    def test_finalize_no_duplicate_intro(self):
        intro = format_related_articles_intro(3, intent="headlines")
        doubled = f"{intro}\n\n{intro}"
        linkable = [{"id": "1", "title": "Story A"}, {"id": "2", "title": "Story B"}]
        out = finalize_reply_with_article_cards(doubled, linkable, intent="headlines")
        self.assertEqual(out.count("Tap any card below"), 1)
        self.assertEqual(_normalize_text(out), _normalize_text(intro))

    def test_finalize_skips_double_intro_from_fallback(self):
        intro = format_related_articles_intro(3, intent="headlines")
        linkable = [{"id": "1", "title": "Story A"}]
        out = finalize_reply_with_article_cards(intro, linkable, intent="headlines")
        self.assertEqual(_normalize_text(out), _normalize_text(intro))

    def test_summarize_compose(self):
        articles = [
            {"id": "1", "title": "A", "summary": "Markets rose today on strong earnings."},
            {"id": "2", "title": "B", "summary": "Leaders met to discuss trade policy."},
        ]
        body = build_local_summary_paragraph(articles)
        out = finalize_summarize_reply(body, articles, articles)
        self.assertIn("Markets rose", out)
        self.assertIn("related articles from TRAK", out.lower())
        self.assertEqual(out.count("Markets rose"), 1)

