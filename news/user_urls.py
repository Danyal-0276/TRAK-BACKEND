from django.urls import path

from . import user_views
from .article_image_proxy import ArticleImageProxyView

urlpatterns = [
    path("articles/image-proxy/", ArticleImageProxyView.as_view(), name="user-article-image-proxy"),
    path("feed/", user_views.UserFeedView.as_view(), name="user-feed"),
    path("explore/", user_views.ExploreFeedView.as_view(), name="user-explore"),
    path("pics/", user_views.PicsFeedView.as_view(), name="user-pics"),
    path("bootstrap/", user_views.UserBootstrapView.as_view(), name="user-bootstrap"),
    path("platform-categories/", user_views.PlatformCategoriesView.as_view(), name="user-platform-categories"),
    path("keywords/", user_views.UserKeywordsView.as_view(), name="user-keywords"),
    path("track-keywords/", user_views.TrackKeywordsView.as_view(), name="user-track-keywords"),
    path("reports/", user_views.ArticleReportView.as_view(), name="user-article-report"),
    path("feedback/", user_views.UserFeedbackView.as_view(), name="user-feedback"),
    path("articles/<str:article_id>/", user_views.ArticleDetailView.as_view(), name="user-article-detail"),
    path("article-tts/plan/", user_views.ArticleTtsPlanView.as_view(), name="user-article-tts-plan"),
    path("article-tts/chunk/", user_views.ArticleTtsChunkView.as_view(), name="user-article-tts-chunk"),
    path("article-tts/chunks/", user_views.ArticleTtsChunksView.as_view(), name="user-article-tts-chunks"),
    path("article-tts/", user_views.ArticleTtsView.as_view(), name="user-article-tts"),
    path("chatbot/", user_views.ChatbotView.as_view(), name="user-chatbot"),
    path("chatbot/conversations/", user_views.ChatbotConversationsView.as_view(), name="user-chatbot-conversations"),
    path(
        "chatbot/conversations/<str:conversation_id>/",
        user_views.ChatbotConversationDetailView.as_view(),
        name="user-chatbot-conversation-detail",
    ),
    path("chatbot/history/", user_views.ChatbotHistoryView.as_view(), name="user-chatbot-history"),
    path("preferences/", user_views.UserPreferencesView.as_view(), name="user-preferences"),
    path("bookmarks/", user_views.BookmarkListCreateView.as_view(), name="user-bookmarks"),
    path("bookmarks/<str:article_id>/", user_views.BookmarkDeleteView.as_view(), name="user-bookmark-delete"),
    path("reactions/", user_views.ReactionView.as_view(), name="user-reactions"),
]
