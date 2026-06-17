from django.test import SimpleTestCase

from news.management.commands.scrape_raw_news import _fair_per_source_caps
from news.scrape_sources import fair_caps_for_ids
from news.schedule.scrape_scheduler import (
    DAILY_SCRAPE_ARTICLE_LIMIT,
    DAILY_SCRAPE_INTERVAL_HOURS,
    _article_limit,
    _interval_hours,
)


class ScrapeSchedulerPolicyTests(SimpleTestCase):
    def test_daily_policy_constants(self):
        self.assertEqual(DAILY_SCRAPE_INTERVAL_HOURS, 24)
        self.assertEqual(DAILY_SCRAPE_ARTICLE_LIMIT, 150)
        self.assertEqual(_interval_hours(), 24)
        self.assertEqual(_article_limit(), 150)

    def test_fair_caps_never_exceed_total(self):
        sources = ["currents", "newsdata", "gnews", "rss", "generic_sites", "dunya", "dawn"]
        caps = _fair_per_source_caps(sources, per_source_limit=150, total_limit=150)
        self.assertEqual(sum(caps.values()), 150)

    def test_connection_fair_caps_for_38_sources(self):
        ids = [f"src-{i}" for i in range(38)]
        caps = fair_caps_for_ids(ids, 150)
        self.assertEqual(len(caps), 38)
        self.assertEqual(sum(caps.values()), 150)
        self.assertTrue(all(v in (3, 4) for v in caps.values()))

    def test_get_last_scrape_insert_count_defaults_zero(self):
        from news.management.commands.scrape_raw_news import get_last_scrape_insert_count

        self.assertEqual(get_last_scrape_insert_count(), 0)
