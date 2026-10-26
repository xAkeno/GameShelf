from rest_framework import permissions
from django.contrib.auth import get_user_model

User = get_user_model()

class IsModeratorOrAdmin(permissions.BasePermission):
    """Allows access only to moderators or admins."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated 
            and request.user.role in [User.Role.MODERATOR, User.Role.ADMIN]
        )

class IsAdminUserRole(permissions.BasePermission):
    """Allows access only to admin users."""
    def has_permission(self, request, view):
        return (
            request.user.is_authenticated 
            and request.user.role == User.Role.ADMIN
        )