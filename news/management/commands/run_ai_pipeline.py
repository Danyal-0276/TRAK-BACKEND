from django.core.management.base import BaseCommand

from news.credibility.inference import preload_credibility_model
from news.summarization.inference import preload_summarizer_model
from news.mongo_db import ensure_all_article_indexes, processed_collection, raw_collection
from news.pipeline import orchestrator


class Command(BaseCommand):
    help = (
        "Run AI pipeline on pending raw_articles → upsert processed_articles "
        "(BART summary, clean_text, spaCy NER, topic_keywords). No extra collections."
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
            "--no-preload-model",
            action="store_true",
            help="Skip eager-loading the HF credibility model at startup.",
        )

    def handle(self, *args, **options):
        ensure_all_article_indexes()
        proc_name = processed_collection().name
        raw_name = raw_collection().name
        self.stdout.write(f"Target collection: {proc_name} (upsert by canonical_url)")

        if options["reprocess"]:
            n = orchestrator.mark_raw_for_reprocess(include_failed=True)
            self.stdout.write(self.style.NOTICE(f"Queued {n} raw article(s) for reprocess."))

        if not options["no_preload_model"]:
            cred_info = preload_credibility_model()
            sum_info = preload_summarizer_model()
            self.stdout.write(f"Credibility loader: {cred_info}")
            self.stdout.write(f"Summarizer loader: {sum_info}")

        if options["all"]:
            result = orchestrator.run_until_empty(batch_size=max(1, options["batch_size"]))
        else:
            result = orchestrator.run_batch(limit=max(1, options["limit"]))

        pending = raw_collection().count_documents({"pipeline_status": "pending"})
        processed = processed_collection().count_documents({})
        self.stdout.write(
            self.style.SUCCESS(
                f"{result} | processed_articles count={processed} | raw pending={pending}"
            )
        )
