from rest_framework import serializers

from apps.core.models import UserGameList

# If this import fails, use the explicit path:
# from apps.core.models.library import UserGameList


class UserGameListSerializer(serializers.ModelSerializer):
    """One row of the user's list, flattened for the frontend."""

    game_id = serializers.IntegerField(source="game.id", read_only=True)
    steam_app_id = serializers.SerializerMethodField()
    title = serializers.CharField(source="game.title", read_only=True)
    cover_image_url = serializers.CharField(
        source="game.cover_image_url", read_only=True, allow_null=True
    )
    genres = serializers.SerializerMethodField()

    class Meta:
        model = UserGameList
        fields = [
            "id",
            "game_id",
            "steam_app_id",
            "title",
            "cover_image_url",
            "genres",
            "status",
            "progress",
            "started_at",
            "completed_at",
            "notes",
        ]
        read_only_fields = fields

    def get_steam_app_id(self, obj):
        # Filter in Python so the prefetched external_ids are reused.
        ext = next(
            (e for e in obj.game.external_ids.all() if e.source == "steam"), None
        )
        if ext is None:
            return None
        try:
            return int(ext.external_id)
        except (TypeError, ValueError):
            return None

    def get_genres(self, obj):
        return [genre.name for genre in obj.game.genres.all()]


class AddToListSerializer(serializers.Serializer):
    """POST /list/  ->  add a Steam game (or change its status)."""

    steam_app_id = serializers.IntegerField(min_value=1)
    status = serializers.ChoiceField(
        choices=UserGameList.Status.choices,
        default=UserGameList.Status.PLAN_TO_PLAY,
    )


class UpdateListEntrySerializer(serializers.ModelSerializer):
    """PATCH /list/<id>/"""

    class Meta:
        model = UserGameList
        fields = ["status", "progress", "started_at", "completed_at", "notes"]

    def validate(self, attrs):
        started = attrs.get("started_at", getattr(self.instance, "started_at", None))
        completed = attrs.get(
            "completed_at", getattr(self.instance, "completed_at", None)
        )
        if started and completed and completed < started:
            raise serializers.ValidationError(
                "completed_at can't be earlier than started_at."
            )
        return attrs