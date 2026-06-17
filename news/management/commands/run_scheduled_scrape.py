"""One-shot scheduled scrape + pipeline (for Render Cron, systemd, or manual runs).

Examples::

    python manage.py run_scheduled_scrape
"""

from django.core.management.base import BaseCommand

from news.schedule.scrape_scheduler import maybe_run_scheduled_scrape


class Command(BaseCommand):
    help = (
        "Scrape up to SCRAPE_SCHEDULE_TOTAL_LIMIT articles (hard cap, fair per admin connection) "
        "and run the AI pipeline. Runs at most once every 24 hours (cron/systemd)."
    )

    def handle(self, *args, **options):
        ran = maybe_run_scheduled_scrape(reason="cli")
        if ran:
            self.stdout.write(self.style.SUCCESS("Scheduled scrape cycle finished."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Skipped (disabled, already ran within 24h, or lock held by another process)."
                )
            )
