from django.urls import path

from . import user_views

urlpatterns = [
    path("feed/", user_views.UserFeedView.as_view(), name="user-feed"),
    path("track-keywords/", user_views.TrackKeywordsView.as_view(), name="user-track-keywords"),
    path("articles/<str:article_id>/", user_views.ArticleDetailView.as_view(), name="user-article-detail"),
]
