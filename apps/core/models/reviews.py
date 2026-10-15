from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from .base import SoftDeleteModel, TimeStampedModel

USER = settings.AUTH_USER_MODEL


class Rating(TimeStampedModel):
    """One active 1-10 rating per user per game. Removing a rating deletes the row."""

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="ratings")
    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="ratings")
    score = models.SmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)])

    class Meta:
        db_table = "ratings"
        constraints = [
            models.UniqueConstraint(fields=["user", "game"], name="uniq_rating_user_game"),
            models.CheckConstraint(
                condition=Q(score__gte=1) & Q(score__lte=10), name="rating_score_1_10"
            ),
        ]


class Review(TimeStampedModel, SoftDeleteModel):
    class Status(models.TextChoices):
        PUBLISHED = "published"
        PENDING = "pending"
        HIDDEN = "hidden"
        REMOVED = "removed"

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="reviews")
    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="reviews")
    title = models.CharField(max_length=255, null=True, blank=True)
    content = models.TextField()  # sanitize server-side
    contains_spoilers = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PUBLISHED)

    class Meta:
        db_table = "reviews"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "game"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_review_user_game",
            )
        ]
        indexes = [models.Index(fields=["game", "status", "created_at"], name="review_game_status_idx")]


class ReviewLike(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="review_likes")
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "review_likes"
        constraints = [models.UniqueConstraint(fields=["user", "review"], name="uniq_review_like")]


class ReviewComment(TimeStampedModel, SoftDeleteModel):
    class Status(models.TextChoices):
        PUBLISHED = "published"
        HIDDEN = "hidden"
        REMOVED = "removed"

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="review_comments")
    parent_comment = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies"
    )
    content = models.TextField()  # text only, no links (validate in the serializer)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PUBLISHED)

    class Meta:
        db_table = "review_comments"
        indexes = [models.Index(fields=["review", "created_at"], name="comment_review_created_idx")]


class CommentAttachment(models.Model):
    class MediaType(models.TextChoices):
        IMAGE = "image"
        GIF = "gif"

    comment = models.ForeignKey(ReviewComment, on_delete=models.CASCADE, related_name="attachments")
    media_type = models.CharField(max_length=10, choices=MediaType.choices)
    file_url = models.TextField()  # uploaded files only, never external URLs
    file_size = models.PositiveIntegerField()  # bytes
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "comment_attachments"


class CommentLike(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="comment_likes")
    comment = models.ForeignKey(ReviewComment, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "comment_likes"
        constraints = [models.UniqueConstraint(fields=["user", "comment"], name="uniq_comment_like")]
