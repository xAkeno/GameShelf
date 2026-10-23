import re

from django.utils.html import strip_tags
from rest_framework import serializers
from django.db.models import Avg, Count
from django.utils.html import strip_tags
from rest_framework import serializers

from apps.core.models import CommentAttachment, Game, Rating, Review, ReviewComment

# Comments are text-only: no links (see the model note on ReviewComment.content).
LINK_PATTERN = re.compile(
    r"(https?://|www\.|\b[\w-]+\.(?:com|net|org|io|gg|tv|ly|co|me|app|xyz|dev)\b)",
    re.IGNORECASE,
)


def _author(user):
    return {"id": user.pk, "username": user.get_username()}


def _is_mine(serializer, obj):
    request = serializer.context.get("request")
    return bool(
        request and request.user.is_authenticated and obj.user_id == request.user.id
    )

def _game_to_app(app_ids):
    """{Game id: steam app id} from the external_ids table (source + external_id)."""
    model = Game._meta.get_field("external_ids").related_model
    links = model.objects.filter(
        source="steam", external_id__in=[str(a) for a in app_ids]
    ).values_list("game_id", "external_id")
    return {game_id: int(ext) for game_id, ext in links}


def rating_stats_for(app_ids, user=None):
    """
    Batched version of _rating_stats for many Steam app ids at once.
    Returns {app_id: {"my_score": int|None, "average": float|None, "count": int}}.
    Read-only: never creates a Game or calls Steam.
    """
    stats = {a: {"my_score": None, "average": None, "count": 0} for a in app_ids}

    game_to_app = _game_to_app(app_ids)
    if not game_to_app:
        return stats
    game_ids = list(game_to_app)

    rows = (
        Rating.objects.filter(game_id__in=game_ids)
        .values("game_id")
        .annotate(average=Avg("score"), count=Count("id"))
    )
    for row in rows:
        entry = stats[game_to_app[row["game_id"]]]
        entry["average"] = round(float(row["average"]), 1)
        entry["count"] = row["count"]

    if user is not None and user.is_authenticated:
        mine = Rating.objects.filter(user=user, game_id__in=game_ids).values_list(
            "game_id", "score"
        )
        for game_id, score in mine:
            stats[game_to_app[game_id]]["my_score"] = score

    return stats

# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------
class RatingWriteSerializer(serializers.Serializer):
    score = serializers.IntegerField(min_value=1, max_value=10)


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------
class ReviewSerializer(serializers.ModelSerializer):
    """
    Read serializer. Expects the queryset from `_reviews_qs` in review_views.py,
    which annotates like_count, comment_count, liked_by_me and author_score.
    """

    author = serializers.SerializerMethodField()
    rating = serializers.IntegerField(
        source="author_score", read_only=True, allow_null=True
    )
    like_count = serializers.IntegerField(read_only=True)
    comment_count = serializers.IntegerField(read_only=True)
    liked_by_me = serializers.BooleanField(read_only=True)
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = [
            "id",
            "author",
            "title",
            "content",
            "contains_spoilers",
            "created_at",
            "edited_at",
            "rating",
            "like_count",
            "comment_count",
            "liked_by_me",
            "is_mine",
        ]

    def get_author(self, obj):
        return _author(obj.user)

    def get_is_mine(self, obj):
        return _is_mine(self, obj)


class ReviewWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["title", "content", "contains_spoilers"]

    def validate_title(self, value):
        # Content is also stripped of HTML; the frontend renders it as plain text.
        return strip_tags(value or "").strip() or None

    def validate_content(self, value):
        value = strip_tags(value).strip()
        if len(value) < 10:
            raise serializers.ValidationError(
                "Please write at least 10 characters."
            )
        if len(value) > 5000:
            raise serializers.ValidationError(
                "Reviews can be at most 5,000 characters."
            )
        return value


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------
class CommentAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = CommentAttachment
        fields = ["id", "media_type", "file_url", "width", "height"]

    def get_file_url(self, obj):
        request = self.context.get("request")
        if request and obj.file_url.startswith("/"):
            return request.build_absolute_uri(obj.file_url)
        return obj.file_url


class CommentSerializer(serializers.ModelSerializer):
    """
    Read serializer. Expects the queryset from `_comments_qs` in review_views.py
    (annotates like_count and liked_by_me, prefetches attachments).
    Replies are returned flat; `parent_comment` says which comment they answer.
    """

    author = serializers.SerializerMethodField()
    parent_comment = serializers.IntegerField(
        source="parent_comment_id", read_only=True, allow_null=True
    )
    like_count = serializers.IntegerField(read_only=True)
    liked_by_me = serializers.BooleanField(read_only=True)
    is_mine = serializers.SerializerMethodField()
    attachments = CommentAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = ReviewComment
        fields = [
            "id",
            "author",
            "content",
            "created_at",
            "edited_at",
            "parent_comment",
            "like_count",
            "liked_by_me",
            "is_mine",
            "attachments",
        ]

    def get_author(self, obj):
        return _author(obj.user)

    def get_is_mine(self, obj):
        return _is_mine(self, obj)


def _clean_comment_text(value):
    value = strip_tags(value or "").strip()
    if LINK_PATTERN.search(value):
        raise serializers.ValidationError("Links aren't allowed in comments.")
    return value


class CommentCreateSerializer(serializers.Serializer):
    # Content may be empty when the comment is just an image (checked in the view).
    content = serializers.CharField(
        allow_blank=True, required=False, default="", max_length=1000
    )
    parent_comment = serializers.IntegerField(required=False, allow_null=True)

    def validate_content(self, value):
        return _clean_comment_text(value)


class CommentEditSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=1000)

    def validate_content(self, value):
        value = _clean_comment_text(value)
        if not value:
            raise serializers.ValidationError("Comment can't be empty.")
        return value