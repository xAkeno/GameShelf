from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from .base import SoftDeleteModel, TimeStampedModel
<<<<<<< HEAD

=======
>>>>>>> 205125e9e5dfac2f37b36d4af8e3adbfc783c38b
USER = settings.AUTH_USER_MODEL


class UserGameList(TimeStampedModel):
    class Status(models.TextChoices):
        PLAYING = "playing"  # UI label can read "Ongoing"
        COMPLETED = "completed"
        ON_HOLD = "on_hold"
        DROPPED = "dropped"
        PLAN_TO_PLAY = "plan_to_play"

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="game_list")
    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="list_entries")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLAN_TO_PLAY)
    progress = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateField(null=True, blank=True)
    completed_at = models.DateField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "user_game_lists"
        constraints = [
            models.UniqueConstraint(fields=["user", "game"], name="uniq_list_user_game"),
            models.CheckConstraint(
                condition=Q(completed_at__isnull=True)
                | Q(started_at__isnull=True)
                | Q(completed_at__gte=F("started_at")),
                name="list_completed_after_started",
            ),
        ]
        indexes = [models.Index(fields=["user", "status"], name="list_user_status_idx")]


class UserGenre(models.Model):
    """Preferred genres chosen at onboarding, used for cold-start recommendations."""

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="preferred_genres")
    genre = models.ForeignKey("Genre", on_delete=models.CASCADE)
    preference_weight = models.DecimalField(
        max_digits=4, decimal_places=3, default=1,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )

    class Meta:
        db_table = "user_genres"
        constraints = [
            models.UniqueConstraint(fields=["user", "genre"], name="uniq_user_genre"),
            models.CheckConstraint(
                condition=Q(preference_weight__gte=0) & Q(preference_weight__lte=1),
                name="user_genre_weight_0_1",
            ),
        ]


class UserDismissedGame(models.Model):
    """'Not Interested'. Recommendations must never show these games."""

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="dismissed_games")
    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="dismissed_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_dismissed_games"
        constraints = [
            models.UniqueConstraint(fields=["user", "game"], name="uniq_dismissed_user_game")
        ]


class GameLike(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="game_likes")
    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "game_likes"
        constraints = [models.UniqueConstraint(fields=["user", "game"], name="uniq_like_user_game")]


class Playlist(TimeStampedModel, SoftDeleteModel):
    class Visibility(models.TextChoices):
        PUBLIC = "public"      # listed everywhere
        UNLISTED = "unlisted"  # anyone with the link, hidden from browse/search
        PRIVATE = "private"    # owner only

    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="playlists")
    title = models.CharField(max_length=150)
    description = models.TextField(null=True, blank=True)
    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.PRIVATE
    )
    cover_image_url = models.TextField(null=True, blank=True)
    games = models.ManyToManyField("Game", through="PlaylistGame", related_name="playlists")

    class Meta:
        db_table = "playlists"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "title"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_playlist_title_per_user",
            )
        ]
        indexes = [
            models.Index(fields=["user", "visibility"], name="playlist_user_vis_idx"),
            models.Index(fields=["visibility", "created_at"], name="playlist_vis_created_idx"),
        ]

    def __str__(self):
        return self.title


class PlaylistGame(models.Model):
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="entries")
    game = models.ForeignKey("Game", on_delete=models.CASCADE)
    position = models.PositiveIntegerField()
    note = models.CharField(max_length=500, null=True, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "playlist_games"
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["playlist", "game"], name="uniq_playlist_game")
        ]


class PlaylistLike(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="playlist_likes")
    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "playlist_likes"
        constraints = [
            models.UniqueConstraint(fields=["user", "playlist"], name="uniq_playlist_like")
        ]
