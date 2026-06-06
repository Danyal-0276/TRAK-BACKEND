"""Show scheduled scrape policy state (24h window + 100-article cap)."""

from django.core.management.base import BaseCommand

from news.schedule.scrape_scheduler import get_scrape_schedule_status


class Command(BaseCommand):
    help = "Print whether the daily scheduled scrape can run and the last run totals."

    def handle(self, *args, **options):
        status = get_scrape_schedule_status()
        self.stdout.write(f"Policy: once every {status['interval_hours']}h, max {status['article_limit']} articles")
        self.stdout.write(f"Enabled: {status['enabled']}")
        self.stdout.write(f"Can run now: {status['can_run_now']}")
        last = status["last_run_at"]
        self.stdout.write(f"Last run (UTC): {last or 'never'}")
        if last:
            self.stdout.write(f"Last scrape inserted: {status['last_scrape_inserted']}/{status['article_limit']}")
            self.stdout.write(f"Next allowed (UTC): {status['next_allowed_at']}")
