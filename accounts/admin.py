from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    ordering = ("email",)
    list_display = ("email", "role", "is_staff", "is_active", "created_at")
    list_filter = ("role", "is_staff", "is_active")
    search_fields = ("email",)
    readonly_fields = ("created_at", "last_login")
    fields = (
        "email",
        "role",
        "is_staff",
        "is_superuser",
        "is_active",
        "created_at",
        "last_login",
    )
