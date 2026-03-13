from django.urls import path
from rest_framework.authtoken import views as auth_views
from . import views

urlpatterns = [
    path("health/", views.health, name="accounts-health"),
    path("api-token-auth/", auth_views.obtain_auth_token, name="api-token-auth"),
]

