"""Tests for fake-news Space response parsing and credibility scores."""

from django.test import SimpleTestCase

from news.credibility.score import compute_credibility_score, effective_credibility_probs
from news.spaces.client import parse_classification_response


class CredibilityParserTests(SimpleTestCase):
    def test_fake_news_space_tuple(self):
        raw = ("REAL", "72%", "28%", "Not found in news", "M1:10 ...")
        parsed = parse_classification_response(raw)
        self.assertEqual(parsed["label_id"], 0)
        self.assertAlmostEqual(parsed["probs"][0], 0.72, places=2)
        self.assertAlmostEqual(parsed["probs"][1], 0.28, places=2)
        score = compute_credibility_score(parsed["probs"])
        self.assertEqual(score, 72)

    def test_scores_differ_by_article(self):
        a = parse_classification_response(("REAL", "82%", "18%", "", ""))
        b = parse_classification_response(("REAL", "61%", "39%", "", ""))
        sa = compute_credibility_score(a["probs"])
        sb = compute_credibility_score(b["probs"])
        self.assertNotEqual(sa, sb)

    def test_legacy_template_resynth(self):
        doc = {
            "credibility_label": 0,
            "credibility_probs": [0.75, 0.125, 0.125],
            "credibility_max_prob": 0.72,
        }
        eff = effective_credibility_probs(doc)
        self.assertAlmostEqual(eff[0], 0.72, places=2)
        self.assertEqual(compute_credibility_score(eff), 77)
