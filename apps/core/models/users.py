from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Greatest, Least
from django.utils import timezone

from .base import TimeStampedModel

USER = settings.AUTH_USER_MODEL


class User(AbstractUser):
    """Custom user. AbstractUser already provides username, password, is_active,
    is_staff and date_joined (exposed here as created_at)."""

    class Role(models.TextChoices):
        USER = "user"
        MODERATOR = "moderator"
        ADMIN = "admin"

    class Visibility(models.TextChoices):
        PUBLIC = "public"
        FRIENDS = "friends"
        PRIVATE = "private"

    email = models.EmailField(max_length=254, unique=True)
    nickname = models.CharField(max_length=100, null=True, blank=True)
    bio = models.TextField(null=True, blank=True)
    avatar_url = models.TextField(null=True, blank=True)
    banner_url = models.TextField(null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USER)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    library_visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    friends_visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    suspended_until = models.DateTimeField(null=True, blank=True)
    ban_reason = models.TextField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users"

    @property
    def created_at(self):
        return self.date_joined

    @property
    def is_moderator(self):
        return self.role in (self.Role.MODERATOR, self.Role.ADMIN)

    @property
    def is_suspended(self):
        return self.suspended_until is not None and self.suspended_until > timezone.now()


class Friendship(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        BLOCKED = "blocked"

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="friendships_sent")
    friend = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="friendships_received")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        db_table = "friendships"
        constraints = [
            models.UniqueConstraint(fields=["user", "friend"], name="uniq_friendship_pair"),
            models.CheckConstraint(condition=~Q(user=F("friend")), name="friendship_not_self"),
            # stops A->B and B->A both existing
            models.UniqueConstraint(
                Least("user", "friend"), Greatest("user", "friend"),
                name="uniq_friendship_either_way",
            ),
        ]
        indexes = [models.Index(fields=["user", "status"], name="friendship_user_status_idx")]


class UserFavoriteGame(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="favorite_games")
    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="favorited_by")
    position = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_favorite_games"
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["user", "game"], name="uniq_favorite_user_game"),
            models.UniqueConstraint(fields=["user", "position"], name="uniq_favorite_user_position"),
            models.CheckConstraint(condition=Q(position__gte=1), name="favorite_position_gte_1"),
        ]


class UserSanction(models.Model):
    class Type(models.TextChoices):
        WARNING = "warning"
        SUSPENSION = "suspension"
        BAN = "ban"

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="sanctions")
    moderator = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="sanctions_applied")
    sanction_type = models.CharField(max_length=20, choices=Type.choices)
    reason = models.TextField()
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_sanctions"
        ordering = ["-created_at"]


class SanctionAppeal(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open"
        UPHELD = "upheld"
        OVERTURNED = "overturned"

    sanction = models.ForeignKey(UserSanction, on_delete=models.CASCADE, related_name="appeals")
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="appeals")
    message = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    reviewed_by = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="appeals_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sanction_appeals"


class Notification(models.Model):
    class Type(models.TextChoices):
        FRIEND_REQUEST = "friend_request"
        COMMENT_REPLY = "comment_reply"
        LIKE = "like"
        REPORT_UPDATE = "report_update"
        SANCTION = "sanction"

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=30, choices=Type.choices)
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "read_at"], name="notification_user_read_idx")]
