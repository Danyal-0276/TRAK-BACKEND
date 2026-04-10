from django.core.management.base import BaseCommand

from news.credibility.inference import preload_credibility_model
from news.mongo_db import ensure_all_article_indexes
from news.pipeline import orchestrator


class Command(BaseCommand):
    help = "Run AI pipeline on pending raw_articles (credibility, summary stub, NER stub)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument(
            "--no-preload-model",
            action="store_true",
            help="Skip eager-loading the HF credibility model at startup.",
        )

    def handle(self, *args, **options):
        ensure_all_article_indexes()
        if not options["no_preload_model"]:
            info = preload_credibility_model()
            self.stdout.write(f"Credibility loader: {info}")
        result = orchestrator.run_batch(limit=options["limit"])
        self.stdout.write(self.style.SUCCESS(str(result)))
