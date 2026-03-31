from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="auth-register"),
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("token/refresh/", views.ThrottledTokenRefreshView.as_view(), name="auth-token-refresh"),
    path("me/", views.MeView.as_view(), name="auth-me"),
    path(
        "password-reset/",
        views.PasswordResetRequestView.as_view(),
        name="auth-password-reset",
    ),
    path(
        "password-reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
        name="auth-password-reset-confirm",
    ),
]
