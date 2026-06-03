from django.apps import AppConfig


class NewsConfig(AppConfig):
    default_auto_field = 'django_mongodb_backend.fields.ObjectIdAutoField'
    name = 'news'

    def ready(self) -> None:
        from news.pipeline.auto_runner import start_auto_pipeline_worker

        start_auto_pipeline_worker()
