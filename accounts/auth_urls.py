from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="auth-register"),
    path("login/", views.LoginView.as_view(), name="auth-login"),
    path("otp/request/", views.OtpRequestView.as_view(), name="auth-otp-request"),
    path("otp/verify/", views.OtpVerifyView.as_view(), name="auth-otp-verify"),
    path("social/providers/", views.SocialProvidersView.as_view(), name="auth-social-providers"),
    path("social/<str:provider>/start/", views.SocialStartView.as_view(), name="auth-social-start"),
    path("social/<str:provider>/callback/", views.SocialCallbackView.as_view(), name="auth-social-callback"),
    path("social/complete/", views.SocialCompleteView.as_view(), name="auth-social-complete"),
    path("token/refresh/", views.ThrottledTokenRefreshView.as_view(), name="auth-token-refresh"),
    path("me/", views.MeView.as_view(), name="auth-me"),
    path("profile/", views.ProfileView.as_view(), name="auth-profile"),
    path("verify/request/", views.VerifyContactRequestView.as_view(), name="auth-verify-request"),
    path("verify/confirm/", views.VerifyContactConfirmView.as_view(), name="auth-verify-confirm"),
    path("follow/", views.FollowView.as_view(), name="auth-follow"),
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
