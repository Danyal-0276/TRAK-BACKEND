"""Welcome-back and inactive-user notifications on login."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model

from notifications.delivery import create_notification

User = get_user_model()
INACTIVE_DAYS = 7


def maybe_welcome_back_notification(user, *, previous_last_login=None) -> None:
    """Notify when a user returns after being away."""
    if not user or not user.pk:
        return
    if getattr(user, "role", None) == User.Role.ADMIN:
        return

    prev = previous_last_login
    if prev is None:
        prev = user.last_login
    if not prev:
        return

    if timezone.is_naive(prev):
        prev = timezone.make_aware(prev, timezone.utc)

    away = datetime.now(timezone.utc) - prev
    if away < timedelta(days=INACTIVE_DAYS):
        return

    days = away.days
    create_notification(
        user.pk,
        ntype="welcome_back",
        text=f"Welcome back! You have been away for {days} days. Check your feed for new stories.",
        details="Your keyword feed may have new articles since your last visit.",
        audience="user",
        title="Welcome back to TRAK",
        dedupe_key=f"welcome_back:{user.pk}",
        dedupe_hours=24 * 7,
    )
