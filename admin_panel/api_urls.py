from django.urls import path

from . import api_views

urlpatterns = [
    path("articles/", api_views.AdminArticlesView.as_view(), name="admin-articles"),
    path("analytics/", api_views.AdminAnalyticsView.as_view(), name="admin-analytics"),
    path("model-metrics/", api_views.AdminModelMetricsView.as_view(), name="admin-model-metrics"),
    path("pipeline/run/", api_views.AdminPipelineRunView.as_view(), name="admin-pipeline-run"),
]
