from django.urls import path

from . import views

urlpatterns = [
    path("", views.NotificationsListView.as_view(), name="notifications-list"),
    path("preferences/", views.NotificationPreferencesView.as_view(), name="notifications-preferences"),
    path("device-token/", views.DeviceTokenRegisterView.as_view(), name="notifications-device-token"),
    path("mark-all-read/", views.MarkAllNotificationsReadView.as_view(), name="notifications-mark-all-read"),
    path("<str:notification_id>/", views.NotificationDetailView.as_view(), name="notifications-detail"),
    path("<str:notification_id>/mark-read/", views.MarkNotificationReadView.as_view(), name="notifications-mark-read"),
]

