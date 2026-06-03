"""Requeue failed raw articles (transient errors) or all failed for retry."""

from django.core.management.base import BaseCommand

from news.mongo_db import raw_collection
from news.pipeline import orchestrator
from news.pipeline.errors import is_transient_pipeline_error


class Command(BaseCommand):
    help = "Requeue failed raw_articles for another pipeline pass."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Requeue every failed article, not only transient errors.",
        )

    def handle(self, *args, **options):
        if options["all"]:
            n = orchestrator.mark_raw_for_reprocess(include_failed=True)
            self.stdout.write(self.style.SUCCESS(f"Queued {n} article(s) for reprocess."))
            return

        n = orchestrator.requeue_transient_failures()
        failed_left = raw_collection().count_documents({"pipeline_status": "failed"})
        self.stdout.write(
            self.style.SUCCESS(
                f"Requeued {n} transient failure(s). {failed_left} still marked failed."
            )
        )
        if failed_left:
            sample = raw_collection().find_one(
                {"pipeline_status": "failed"},
                {"pipeline_error": 1},
            )
            if sample:
                self.stdout.write(f"Sample error: {(sample.get('pipeline_error') or '')[:200]}")
