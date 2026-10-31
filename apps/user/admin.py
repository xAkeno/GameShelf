from django.contrib import admin
from django.contrib.auth import get_user_model
from apps.core.models import Friendship  # Adjust this import path if your core app path differs

User = get_user_model()

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["id", "username", "email", "is_staff", "created_at","birth_day"]
    search_fields = ["username", "email"]

@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "friend", "status", "created_at", ]
    list_filter = ["status"]