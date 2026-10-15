from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from .base import TimeStampedModel, exactly_one

USER = settings.AUTH_USER_MODEL


class BaseReport(TimeStampedModel):
    """Shared columns for review, comment and playlist reports."""

    class Reason(models.TextChoices):
        SPAM = "spam"
        HARASSMENT = "harassment"
        SPOILERS = "spoilers"
        HATE = "hate"
        MISINFORMATION = "misinformation"
        OTHER = "other"

    class Status(models.TextChoices):
        OPEN = "open"
        REVIEWING = "reviewing"
        RESOLVED = "resolved"
        DISMISSED = "dismissed"

    reporter = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="%(class)s_filed")
    reason = models.CharField(max_length=30, choices=Reason.choices)
    details = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="%(class)s_resolved"
    )

    class Meta:
        abstract = True


class ReviewReport(BaseReport):
    review = models.ForeignKey("Review", on_delete=models.CASCADE, related_name="reports")

    class Meta:
        db_table = "review_reports"
        constraints = [
            models.UniqueConstraint(fields=["reporter", "review"], name="uniq_review_report_reporter")
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="review_report_status_idx")]


class CommentReport(BaseReport):
    comment = models.ForeignKey("ReviewComment", on_delete=models.CASCADE, related_name="reports")

    class Meta:
        db_table = "comment_reports"
        constraints = [
            models.UniqueConstraint(fields=["reporter", "comment"], name="uniq_comment_report_reporter")
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="comment_report_status_idx")]


class PlaylistReport(BaseReport):
    playlist = models.ForeignKey("Playlist", on_delete=models.CASCADE, related_name="reports")

    class Meta:
        db_table = "playlist_reports"
        constraints = [
            models.UniqueConstraint(fields=["reporter", "playlist"], name="uniq_playlist_report_reporter")
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="playlist_report_status_idx")]


class ModerationFlag(TimeStampedModel):
    """Suspicious activity found by the detection job or a moderator.
    Flags are for a human to review, never an automatic delete."""

    class FlagType(models.TextChoices):
        REVIEW_BOMB = "review_bomb"
        SPAM = "spam"
        ABUSE = "abuse"
        DUPLICATE = "duplicate"
        RATE_LIMIT = "rate_limit"
        OTHER = "other"

    class Status(models.TextChoices):
        OPEN = "open"
        REVIEWING = "reviewing"
        CONFIRMED = "confirmed"
        DISMISSED = "dismissed"

    # exactly one target must be set
    user = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.CASCADE, related_name="flags"
    )
    game = models.ForeignKey(
        "Game", null=True, blank=True, on_delete=models.CASCADE, related_name="flags"
    )
    review = models.ForeignKey(
        "Review", null=True, blank=True, on_delete=models.CASCADE, related_name="flags"
    )
    comment = models.ForeignKey(
        "ReviewComment", null=True, blank=True, on_delete=models.CASCADE, related_name="flags"
    )
    playlist = models.ForeignKey(
        "Playlist", null=True, blank=True, on_delete=models.CASCADE, related_name="flags"
    )

    flag_type = models.CharField(max_length=30, choices=FlagType.choices)
    anomaly_score = models.FloatField(
        null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(1)]
    )
    model_run = models.ForeignKey(
        "ModelRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="flags"
    )
    evidence = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    reviewed_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="flags_reviewed"
    )
    resolution_notes = models.TextField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "moderation_flags"
        constraints = [
            models.CheckConstraint(
                condition=exactly_one("user", "game", "review", "comment", "playlist"),
                name="flag_exactly_one_target",
            ),
            models.CheckConstraint(
                condition=Q(anomaly_score__isnull=True)
                | (Q(anomaly_score__gte=0) & Q(anomaly_score__lte=1)),
                name="flag_anomaly_score_0_1",
            ),
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="flag_status_created_idx")]


class ModerationAction(TimeStampedModel):
    """Audit trail of what moderators did. Never delete these rows; the related
    report/flag links are SET_NULL so the record survives."""

    class ActionType(models.TextChoices):
        HIDE = "hide"
        RESTORE = "restore"
        REMOVE = "remove"
        WARN = "warn"
        SUSPEND = "suspend"
        BAN = "ban"
        DISMISS_REPORT = "dismiss_report"
        OTHER = "other"

    moderator = models.ForeignKey(
        USER, on_delete=models.PROTECT, related_name="moderation_actions"
    )
    action_type = models.CharField(max_length=30, choices=ActionType.choices)
    review = models.ForeignKey(
        "Review", null=True, blank=True, on_delete=models.SET_NULL, related_name="moderation_actions"
    )
    comment = models.ForeignKey(
        "ReviewComment", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="moderation_actions",
    )
    playlist = models.ForeignKey(
        "Playlist", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="moderation_actions",
    )
    report = models.ForeignKey(
        ReviewReport, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions"
    )
    comment_report = models.ForeignKey(
        CommentReport, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions"
    )
    playlist_report = models.ForeignKey(
        PlaylistReport, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions"
    )
    flag = models.ForeignKey(
        ModerationFlag, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions"
    )
    target_user = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="moderation_actions_received",
    )
    reason = models.TextField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "moderation_actions"
        ordering = ["-created_at"]
