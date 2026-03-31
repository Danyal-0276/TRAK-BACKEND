from django.core.management.base import BaseCommand

from news.mongo_db import ensure_all_article_indexes


class Command(BaseCommand):
    help = "Create indexes on raw_articles, processed_articles, user_keywords."

    def handle(self, *args, **options):
        ensure_all_article_indexes()
        self.stdout.write(self.style.SUCCESS("Mongo indexes ensured."))
