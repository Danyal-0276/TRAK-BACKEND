from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from news.services import article_query


class UserFeedView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        try:
            limit = min(int(request.query_params.get("limit", 50)), 100)
        except ValueError:
            limit = 50
        q = (request.query_params.get("q") or "").strip()
        data = article_query.get_user_feed(request.user, limit=limit, search_q=q)
        return Response({"results": data})


class TrackKeywordsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        keywords = request.data.get("keywords")
        if keywords is None:
            return Response({"detail": "keywords required"}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(keywords, list):
            return Response({"detail": "keywords must be a list"}, status=status.HTTP_400_BAD_REQUEST)
        payload = article_query.upsert_user_keywords(request.user, keywords)
        return Response(payload, status=status.HTTP_200_OK)


class ArticleDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, article_id):
        doc = article_query.get_article_by_id(article_id, request.user)
        if doc is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(doc)
