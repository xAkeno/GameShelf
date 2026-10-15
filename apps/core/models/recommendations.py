from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q

from .base import exactly_one

USER = settings.AUTH_USER_MODEL


class ModelRun(models.Model):
    """One execution of an algorithm job. Results point at a run; a run becomes
    active only after it finishes, so pages never read half-written results."""

    class Algorithm(models.TextChoices):
        BAYESIAN = "bayesian"
        SVD = "svd"
        TFIDF = "tfidf"
        KMEANS = "kmeans"
        TRENDING = "trending"
        ISOLATION_FOREST = "isolation_forest"

    class Status(models.TextChoices):
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    algorithm = models.CharField(max_length=30, choices=Algorithm.choices)
    version = models.CharField(max_length=50)
    params = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    is_active = models.BooleanField(default=False)
    metrics = models.JSONField(default=dict, blank=True)
    error = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "model_runs"
        constraints = [
            # at most one active run per algorithm
            models.UniqueConstraint(
                fields=["algorithm"], condition=Q(is_active=True), name="uniq_active_run_per_algorithm"
            )
        ]


class GameScore(models.Model):
    """One row per game: powers sort by rating/popularity and trending.
    Bayesian score = v/(v+m) * R + m/(v+m) * C."""

    game = models.OneToOneField(
        "Game", primary_key=True, on_delete=models.CASCADE, related_name="score"
    )
    average_rating = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    rating_count = models.PositiveIntegerField(default=0)
    bayesian_score = models.DecimalField(max_digits=5, decimal_places=3, null=True, blank=True)
    popularity_score = models.FloatField(default=0)
    trending_score = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "game_scores"
        indexes = [
            models.Index(fields=["-bayesian_score"], name="score_bayesian_idx"),
            models.Index(fields=["-trending_score"], name="score_trending_idx"),
            models.Index(fields=["-popularity_score"], name="score_popularity_idx"),
        ]


class GameSimilarity(models.Model):
    """Top-N similar games per game (TF-IDF + cosine similarity)."""

    model_run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="similarities")
    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="similarities")
    similar_game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="+")
    score = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(1)])
    rank = models.PositiveSmallIntegerField()
    reason = models.JSONField(default=dict, blank=True)  # shared genres / tags

    class Meta:
        db_table = "game_similarities"
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["model_run", "game", "similar_game"], name="uniq_similarity_pair"
            ),
            models.CheckConstraint(condition=~Q(game=F("similar_game")), name="similarity_not_self"),
        ]
        indexes = [models.Index(fields=["game", "model_run", "rank"], name="similarity_lookup_idx")]


class UserRecommendation(models.Model):
    """Per-user recommendations (SVD). When reading, still exclude dismissed,
    completed and already-listed games, since those change between runs."""

    class ReasonType(models.TextChoices):
        BECAUSE_PLAYED = "because_played"
        BECAUSE_LIKED = "because_liked"
        MORE_GENRE = "more_genre"
        COLLABORATIVE = "collaborative"
        TRENDING = "trending"

    model_run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="recommendations")
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="recommendations")
    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="recommended_to")
    score = models.FloatField()
    rank = models.PositiveSmallIntegerField()
    reason_type = models.CharField(max_length=30, choices=ReasonType.choices)
    reason_game = models.ForeignKey(
        "Game", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reason_text = models.CharField(max_length=255, null=True, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_recommendations"
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["model_run", "user", "game"], name="uniq_recommendation_run_user_game"
            )
        ]
        indexes = [models.Index(fields=["user", "model_run", "rank"], name="recommendation_lookup_idx")]


class Cluster(models.Model):
    """K-Means cluster of games or users."""

    class EntityType(models.TextChoices):
        GAME = "game"
        USER = "user"

    model_run = models.ForeignKey(ModelRun, on_delete=models.CASCADE, related_name="clusters")
    entity_type = models.CharField(max_length=10, choices=EntityType.choices)
    label = models.CharField(max_length=100, null=True, blank=True)
    centroid = models.JSONField(default=dict, blank=True)
    size = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "clusters"


class ClusterMember(models.Model):
    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name="members")
    game = models.ForeignKey(
        "Game", null=True, blank=True, on_delete=models.CASCADE, related_name="cluster_memberships"
    )
    user = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.CASCADE, related_name="cluster_memberships"
    )
    distance = models.FloatField(null=True, blank=True)
    pca_x = models.FloatField(null=True, blank=True)  # optional, for visualization
    pca_y = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "cluster_members"
        constraints = [
            models.CheckConstraint(condition=exactly_one("game", "user"), name="cluster_member_one_target"),
            models.UniqueConstraint(fields=["cluster", "game"], name="uniq_cluster_game"),
            models.UniqueConstraint(fields=["cluster", "user"], name="uniq_cluster_user"),
        ]


class GameActivityDaily(models.Model):
    """Per-game daily activity counts; the trending job takes a moving average of these."""

    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="daily_activity")
    date = models.DateField()
    ratings_count = models.PositiveIntegerField(default=0)
    reviews_count = models.PositiveIntegerField(default=0)
    likes_count = models.PositiveIntegerField(default=0)
    list_adds_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "game_activity_daily"
        constraints = [models.UniqueConstraint(fields=["game", "date"], name="uniq_activity_game_date")]


class RecommendationEvent(models.Model):
    class EventType(models.TextChoices):
        IMPRESSION = "impression"
        CLICK = "click"
        DISMISS = "dismiss"  # also create a UserDismissedGame row
        ADD_TO_LIST = "add_to_list"
        RATE = "rate"

    class Placement(models.TextChoices):
        HOME = "home"
        GAME_DETAILS = "game_details"

    user = models.ForeignKey(
        USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="recommendation_events"
    )
    game = models.ForeignKey("Game", on_delete=models.CASCADE, related_name="recommendation_events")
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    algorithm = models.CharField(max_length=100, null=True, blank=True)
    model_run = models.ForeignKey(
        ModelRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    placement = models.CharField(max_length=20, choices=Placement.choices, null=True, blank=True)
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "recommendation_events"
