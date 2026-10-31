from rest_framework import serializers
from django.contrib.auth import get_user_model
from apps.core.models import Game, Playlist, PlaylistGame, UserGameList, PlaylistLike
from django.conf import settings
User = get_user_model()

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
    """POST /list/ -> add a Steam game (or change its status)."""

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
        completed = attrs.get("completed_at", getattr(self.instance, "completed_at", None))
        if started and completed and completed < started:
            raise serializers.ValidationError(
                "completed_at can't be earlier than started_at."
            )
        return attrs


class CreatePlaylistSerializer(serializers.ModelSerializer):
    """POST /playlists/ -> create a playlist from an ordered list of Steam app ids."""

    steam_app_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        write_only=True,
        allow_empty=False,
        max_length=100,
    )

    class Meta:
        model = Playlist
        fields = ["id", "title", "description", "visibility", "cover_image_url", "steam_app_ids"]
        read_only_fields = ["id"]

    def validate_title(self, value):
        user = self.context["request"].user
        if Playlist.objects.filter(
            user=user, title__iexact=value, deleted_at__isnull=True
        ).exists():
            raise serializers.ValidationError("You already have a playlist with this title.")
        return value

    def validate_steam_app_ids(self, ids):
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("A game can only appear once in a playlist.")
        return ids

    def create(self, validated_data):
        validated_data.pop("steam_app_ids", None)
        games = validated_data.pop("games", [])
        playlist = Playlist.objects.create(
            user=self.context["request"].user, **validated_data
        )
        PlaylistGame.objects.bulk_create(
            [PlaylistGame(playlist=playlist, game=g, position=i) for i, g in enumerate(games)]
        )
        return playlist


class PlaylistGameSearchSerializer(serializers.ModelSerializer):
    """Compact game result used by the playlist game's search bar."""

    class Meta:
        model = Game
        fields = ["id", "title", "cover_image_url"]

class AbsoluteMediaURLMixin:
    """Ensures avatar_url and banner_url are returned as absolute URIs."""
    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")

        for field in ("avatar_url", "banner_url"):
            url = data.get(field)
            if url and not url.startswith("http"):
                if request:
                    data[field] = request.build_absolute_uri(url)
                else:
                    # Fallback if request context was omitted in Django View
                    base_url = getattr(settings, "BACKEND_URL", "http://127.0.0.1:8000")
                    data[field] = f"{base_url.rstrip('/')}{url}"

        return data

# --- OWNER SERIALIZER (Fixes missing avatar_url) ---
class PlaylistOwnerSerializer(AbsoluteMediaURLMixin, serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "nickname", "avatar_url", "visibiility", "banner_url", "bio"]

# --- PLAYLIST GAME SERIALIZER ---
class PlaylistGameSerializer(serializers.ModelSerializer):
    steam_appid = serializers.SerializerMethodField()
    cover_url = serializers.CharField(source="game.cover_image_url", read_only=True)
    title = serializers.CharField(source="game.title", read_only=True)
    genres = serializers.SerializerMethodField()

    class Meta:
        model = PlaylistGame
        fields = ["id", "steam_appid", "title", "cover_url", "genres", "position", "note"]

    def get_steam_appid(self, obj):
        ext = next((e for e in obj.game.external_ids.all() if e.source == "steam"), None)
        if ext:
            try:
                return int(ext.external_id)
            except (TypeError, ValueError):
                pass
        return None

    def get_genres(self, obj):
        return [g.name for g in obj.game.genres.all()]


# --- PLAYLIST DETAIL SERIALIZER ---
class PlaylistDetailSerializer(serializers.ModelSerializer):
    # DRF auto-passes context to fields declared like this, 
    # but make sure request is present in view context
    owner = PlaylistOwnerSerializer(source="user", read_only=True)
    games = serializers.SerializerMethodField()
    like_count = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()

    class Meta:
        model = Playlist
        fields = [
            "id",
            "title",
            "description",
            "visibility",
            "cover_image_url",
            "owner",
            "games",
            "like_count",
            "is_liked",
            "created_at",
            "updated_at",
        ]

    def get_games(self, obj):
        pgs = (
            obj.playlist_games.select_related("game")
            .prefetch_related("game__external_ids", "game__genres")
            .order_by("position")
        )
        # Explicitly pass self.context to nested serializers
        return PlaylistGameSerializer(pgs, many=True, context=self.context).data

    def get_like_count(self, obj):
        return obj.likes.count()

    def get_is_liked(self, obj):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            return obj.likes.filter(user=request.user).exists()
        return False