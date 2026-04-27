from django.urls import path

from . import api_views

urlpatterns = [
    path("articles/", api_views.AdminArticlesView.as_view(), name="admin-articles"),
    path("articles/<str:scope>/<str:article_id>/", api_views.AdminArticleDetailView.as_view(), name="admin-article-detail"),
    path("analytics/", api_views.AdminAnalyticsView.as_view(), name="admin-analytics"),
    path("model-metrics/", api_views.AdminModelMetricsView.as_view(), name="admin-model-metrics"),
    path("pipeline/run/", api_views.AdminPipelineRunView.as_view(), name="admin-pipeline-run"),
    path("users/", api_views.AdminUsersView.as_view(), name="admin-users"),
    path("users/<int:user_id>/", api_views.AdminUserDetailView.as_view(), name="admin-user-detail"),
    path("settings/", api_views.AdminSettingsView.as_view(), name="admin-settings"),
    path("notifications/", api_views.AdminNotificationsView.as_view(), name="admin-notifications"),
]
