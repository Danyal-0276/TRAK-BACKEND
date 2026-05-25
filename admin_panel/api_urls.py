from django.urls import path

from . import api_views

urlpatterns = [
    path("articles/", api_views.AdminArticlesView.as_view(), name="admin-articles"),
    path("articles/<str:scope>/<str:article_id>/", api_views.AdminArticleDetailView.as_view(), name="admin-article-detail"),
    path("analytics/", api_views.AdminAnalyticsView.as_view(), name="admin-analytics"),
    path("model-metrics/", api_views.AdminModelMetricsView.as_view(), name="admin-model-metrics"),
    path("pipeline/run/", api_views.AdminPipelineRunView.as_view(), name="admin-pipeline-run"),
    path("users/", api_views.AdminUsersView.as_view(), name="admin-users"),
    path("users/<str:user_id>/", api_views.AdminUserDetailView.as_view(), name="admin-user-detail"),
    path("admins/", api_views.AdminAdminsCreateView.as_view(), name="admin-admins-create"),
    path("settings/", api_views.AdminSettingsView.as_view(), name="admin-settings"),
    path("settings/categories/", api_views.AdminCategoriesView.as_view(), name="admin-categories"),
    path("settings/categories/<str:category_slug>/", api_views.AdminCategoryDetailView.as_view(), name="admin-category-detail"),
    path(
        "settings/categories/<str:category_slug>/subcategories/",
        api_views.AdminCategorySubcategoriesView.as_view(),
        name="admin-category-subcategories",
    ),
    path(
        "settings/categories/<str:category_slug>/subcategories/<str:sub_slug>/",
        api_views.AdminSubcategoryDetailView.as_view(),
        name="admin-subcategory-detail",
    ),
    path("settings/connections/", api_views.AdminConnectionsView.as_view(), name="admin-connections"),
    path(
        "settings/connections/<str:connection_slug>/",
        api_views.AdminConnectionDetailView.as_view(),
        name="admin-connection-detail",
    ),
    path("notifications/", api_views.AdminNotificationsView.as_view(), name="admin-notifications"),
]
 