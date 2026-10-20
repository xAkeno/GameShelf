
from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.core.models import Friendship, Notification

User = get_user_model()


class UserBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "nickname", "avatar_url"]


class SuggestedUserSerializer(serializers.ModelSerializer):
    """Public user information for the Discover Players page."""

    class Meta:
        model = User
        fields = ["id", "username", "nickname", "avatar_url", "bio"]


class UsernameSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)


class FriendshipSerializer(serializers.ModelSerializer):
    """Represent a friendship from the logged-in user's perspective."""

    user = serializers.SerializerMethodField()
    direction = serializers.SerializerMethodField()

    class Meta:
        model = Friendship
        fields = [
            "id",
            "user",
            "status",
            "direction",
            "created_at",
            "updated_at",
        ]

    def get_user(self, obj):
        me = self.context["me"]
        other = obj.friend if obj.user_id == me.id else obj.user
        return UserBriefSerializer(other).data

    def get_direction(self, obj):
        if obj.status != Friendship.Status.PENDING:
            return None

        return (
            "outgoing"
            if obj.user_id == self.context["me"].id
            else "incoming"
        )


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "type", "payload", "read_at", "created_at"]
