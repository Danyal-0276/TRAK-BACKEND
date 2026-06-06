from django.apps import AppConfig


class NewsConfig(AppConfig):
    default_auto_field = 'django_mongodb_backend.fields.ObjectIdAutoField'
    name = 'news'

    def ready(self) -> None:
        from news.pipeline.auto_runner import start_auto_pipeline_worker
        from news.schedule.scrape_scheduler import start_scrape_scheduler

        start_auto_pipeline_worker()
        start_scrape_scheduler()
