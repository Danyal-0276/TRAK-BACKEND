from django.urls import path

from . import user_views

urlpatterns = [
    path("feed/", user_views.UserFeedView.as_view(), name="user-feed"),
    path("explore/", user_views.ExploreFeedView.as_view(), name="user-explore"),
    path("track-keywords/", user_views.TrackKeywordsView.as_view(), name="user-track-keywords"),
    path("articles/<str:article_id>/", user_views.ArticleDetailView.as_view(), name="user-article-detail"),
    path("chatbot/", user_views.ChatbotView.as_view(), name="user-chatbot"),
    path("chatbot/history/", user_views.ChatbotHistoryView.as_view(), name="user-chatbot-history"),
]
