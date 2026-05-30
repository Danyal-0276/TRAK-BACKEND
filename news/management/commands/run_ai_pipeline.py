from django.conf import settings
from django.core.management.base import BaseCommand

from news.credibility.inference import preload_credibility_model
from news.summarization.inference import preload_summarizer_model
from news.mongo_db import ensure_all_article_indexes, processed_collection, raw_collection
from news.pipeline import orchestrator
from news.services.feed_cache import invalidate_explore_cache


class Command(BaseCommand):
    help = (
        "Run AI pipeline on pending raw_articles → upsert processed_articles "
        "(HF Space BART summary, fake detection Space, Google fact-check, spaCy NER, topic_keywords)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument(
            "--all",
            action="store_true",
            help="Process every pending raw article (batched until queue empty).",
        )
        parser.add_argument(
            "--reprocess",
            action="store_true",
            help="Reset done/failed raw_articles to pending first (refresh processed_articles in place).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Batch size when using --all (default 50).",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=None,
            help="Parallel article workers (default: PIPELINE_WORKERS env or 1). Use 3–4 for cron/VPS.",
        )
        parser.add_argument(
            "--requeue-stale",
            action="store_true",
            help="Reset processing articles older than PIPELINE_STALE_MINUTES back to pending.",
        )
        parser.add_argument(
            "--no-preload-model",
            action="store_true",
            help="Skip eager-loading the HF credibility model at startup.",
        )

    def handle(self, *args, **options):
        ensure_all_article_indexes()
        proc_name = processed_collection().name
        raw_name = raw_collection().name
        self.stdout.write(f"Target collection: {proc_name} (upsert by canonical_url)")

        workers = options["workers"]
        if workers is None:
            workers = getattr(settings, "PIPELINE_WORKERS", 1)
        workers = max(1, min(8, int(workers)))

        if options["requeue_stale"]:
            stale_mins = getattr(settings, "PIPELINE_STALE_MINUTES", 30)
            n = orchestrator.requeue_stale_processing(stale_minutes=stale_mins)
            self.stdout.write(self.style.NOTICE(f"Requeued {n} stale processing article(s)."))

        if options["reprocess"]:
            n = orchestrator.mark_raw_for_reprocess(include_failed=True)
            self.stdout.write(self.style.NOTICE(f"Queued {n} raw article(s) for reprocess."))

        if not options["no_preload_model"]:
            cred_info = preload_credibility_model()
            sum_info = preload_summarizer_model()
            self.stdout.write(f"Credibility loader: {cred_info}")
            self.stdout.write(f"Summarizer loader: {sum_info}")

        self.stdout.write(self.style.NOTICE(f"Using {workers} worker(s)."))

        if options["all"]:
            result = orchestrator.run_until_empty(
                batch_size=max(1, options["batch_size"]),
                workers=workers,
            )
        else:
            result = orchestrator.run_batch(limit=max(1, options["limit"]), workers=workers)

        pending = raw_collection().count_documents({"pipeline_status": "pending"})
        processing = raw_collection().count_documents({"pipeline_status": "processing"})
        processed = processed_collection().count_documents({})
        invalidate_explore_cache()
        self.stdout.write(
            self.style.SUCCESS(
                f"{result} | processed_articles count={processed} | "
                f"raw pending={pending} processing={processing}"
            )
        )
