from rest_framework.permissions import BasePermission

from .models import User


class IsAdminRole(BasePermission):
    """Allow only users with role=admin (checked server-side)."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return getattr(user, "role", None) == User.Role.ADMIN
