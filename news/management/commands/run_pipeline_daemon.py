"""Long-running worker: keep draining the raw pending queue (for systemd / manual ops)."""

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from news.pipeline.auto_runner import drain_pending_queue_if_needed


class Command(BaseCommand):
    help = "Poll MongoDB and run the AI pipeline until the pending queue stays empty."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=None,
            help="Seconds between checks (default: PIPELINE_AUTO_INTERVAL_SECONDS).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single drain pass and exit.",
        )

    def handle(self, *args, **options):
        interval = options["interval"]
        if interval is None:
            interval = max(30, int(getattr(settings, "PIPELINE_AUTO_INTERVAL_SECONDS", 90)))

        self.stdout.write(
            self.style.NOTICE(
                f"Pipeline daemon started (interval={interval}s, workers={getattr(settings, 'PIPELINE_WORKERS', 1)})"
            )
        )
        while True:
            result = drain_pending_queue_if_needed(reason="daemon")
            if result is not None:
                self.stdout.write(self.style.SUCCESS(str(result)))
            else:
                self.stdout.write("No pending articles (or lock held).")
            if options["once"]:
                break
            time.sleep(interval)
