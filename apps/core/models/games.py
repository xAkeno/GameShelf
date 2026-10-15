from django.db import models
from django.db.models import F, Q

from .base import TimeStampedModel


class Game(TimeStampedModel):
    class GameType(models.TextChoices):
        BASE = "base"
        DLC = "dlc"
        EXPANSION = "expansion"

    class Source(models.TextChoices):
        API = "api"
        MANUAL = "manual"

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    release_date = models.DateField(null=True, blank=True)
    cover_image_url = models.TextField(null=True, blank=True)
    banner_image_url = models.TextField(null=True, blank=True)
    game_type = models.CharField(max_length=20, choices=GameType.choices, default=GameType.BASE)
    parent_game = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    # fields the API sync must not overwrite, e.g. ["description", "cover_image_url"]
    locked_fields = models.JSONField(default=list, blank=True)

    developers = models.ManyToManyField("Developer", through="GameDeveloper", related_name="games")
    publishers = models.ManyToManyField("Publisher", through="GamePublisher", related_name="games")
    platforms = models.ManyToManyField("Platform", through="GamePlatform", related_name="games")
    genres = models.ManyToManyField("Genre", through="GameGenre", related_name="games")
    tags = models.ManyToManyField("Tag", through="GameTag", related_name="games")
    languages = models.ManyToManyField("Language", through="GameLanguage", related_name="games")

    class Meta:
        db_table = "games"
        indexes = [
            models.Index(fields=["title"], name="game_title_idx"),
            models.Index(fields=["release_date"], name="game_release_date_idx"),
        ]
        constraints = [
            # every DLC/expansion has a parent, no base game does
            models.CheckConstraint(
                condition=Q(game_type="base", parent_game__isnull=True)
                | (~Q(game_type="base") & Q(parent_game__isnull=False)),
                name="game_type_matches_parent",
            ),
        ]

    def __str__(self):
        return self.title


class Developer(TimeStampedModel):
    """Game studio, e.g. FromSoftware, Team Cherry."""

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(null=True, blank=True)
    website_url = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "developers"

    def __str__(self):
        return self.name


class GameDeveloper(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    developer = models.ForeignKey(Developer, on_delete=models.CASCADE)
    role = models.CharField(max_length=30, null=True, blank=True)  # lead, co-developer, porting...

    class Meta:
        db_table = "game_developers"
        constraints = [
            models.UniqueConstraint(fields=["game", "developer"], name="uniq_game_developer")
        ]


class Publisher(TimeStampedModel):
    """Publishing company, e.g. EA, Activision."""

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(null=True, blank=True)
    website_url = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "publishers"

    def __str__(self):
        return self.name


class GamePublisher(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    publisher = models.ForeignKey(Publisher, on_delete=models.CASCADE)
    role = models.CharField(max_length=30, null=True, blank=True)  # primary, regional, distributor

    class Meta:
        db_table = "game_publishers"
        constraints = [
            models.UniqueConstraint(fields=["game", "publisher"], name="uniq_game_publisher")
        ]


class Platform(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    manufacturer = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table = "platforms"

    def __str__(self):
        return self.name


class GamePlatform(models.Model):
    """Has its own id so system_requirements can point at it."""

    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    platform = models.ForeignKey(Platform, on_delete=models.CASCADE)
    release_date = models.DateField(null=True, blank=True)
    min_players = models.SmallIntegerField(null=True, blank=True)
    max_players = models.SmallIntegerField(null=True, blank=True)

    class Meta:
        db_table = "game_platforms"
        constraints = [
            models.UniqueConstraint(fields=["game", "platform"], name="uniq_game_platform"),
            models.CheckConstraint(
                condition=Q(min_players__isnull=True) | Q(min_players__gte=1),
                name="game_platform_min_players_gte_1",
            ),
            models.CheckConstraint(
                condition=Q(max_players__isnull=True)
                | Q(min_players__isnull=True)
                | Q(max_players__gte=F("min_players")),
                name="game_platform_max_gte_min",
            ),
        ]


class Genre(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "genres"

    def __str__(self):
        return self.name


class GameGenre(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    genre = models.ForeignKey(Genre, on_delete=models.CASCADE)

    class Meta:
        db_table = "game_genres"
        constraints = [models.UniqueConstraint(fields=["game", "genre"], name="uniq_game_genre")]


class Tag(TimeStampedModel):
    """Finer-grained than genres (Metroidvania, 2D, Souls-like). Feeds TF-IDF similarity."""

    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=100, unique=True)

    class Meta:
        db_table = "tags"

    def __str__(self):
        return self.name


class GameTag(models.Model):
    class Source(models.TextChoices):
        API = "api"
        MANUAL = "manual"

    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.API)

    class Meta:
        db_table = "game_tags"
        constraints = [models.UniqueConstraint(fields=["game", "tag"], name="uniq_game_tag")]


class Language(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True)  # en, ja

    class Meta:
        db_table = "languages"

    def __str__(self):
        return self.name


class GameLanguage(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    language = models.ForeignKey(Language, on_delete=models.CASCADE)
    audio_supported = models.BooleanField(default=False)
    text_supported = models.BooleanField(default=False)

    class Meta:
        db_table = "game_languages"
        constraints = [
            models.UniqueConstraint(fields=["game", "language"], name="uniq_game_language")
        ]


class Award(TimeStampedModel):
    name = models.CharField(max_length=255)
    organization = models.CharField(max_length=255, null=True, blank=True)
    category = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "awards"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "organization", "category"], name="uniq_award_definition"
            )
        ]

    def __str__(self):
        return self.name


class GameAward(models.Model):
    class Result(models.TextChoices):
        WON = "won"
        NOMINATED = "nominated"
        SHORTLISTED = "shortlisted"

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="awards")
    award = models.ForeignKey(Award, on_delete=models.CASCADE, related_name="game_awards")
    year = models.SmallIntegerField()
    result = models.CharField(max_length=20, choices=Result.choices)

    class Meta:
        db_table = "game_awards"
        constraints = [
            models.UniqueConstraint(fields=["game", "award", "year"], name="uniq_game_award_year")
        ]


class SystemRequirement(TimeStampedModel):
    class Level(models.TextChoices):
        MINIMUM = "minimum"
        RECOMMENDED = "recommended"

    game_platform = models.ForeignKey(
        GamePlatform, on_delete=models.CASCADE, related_name="system_requirements"
    )
    level = models.CharField(max_length=20, choices=Level.choices)
    operating_system = models.CharField(max_length=255, null=True, blank=True)
    processor = models.CharField(max_length=255, null=True, blank=True)
    memory = models.CharField(max_length=100, null=True, blank=True)
    graphics = models.CharField(max_length=255, null=True, blank=True)
    directx = models.CharField(max_length=100, null=True, blank=True)
    storage = models.CharField(max_length=100, null=True, blank=True)
    additional_notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "system_requirements"
        constraints = [
            models.UniqueConstraint(
                fields=["game_platform", "level"], name="uniq_system_requirement_level"
            )
        ]


class GameMedia(models.Model):
    class MediaType(models.TextChoices):
        SCREENSHOT = "screenshot"
        TRAILER = "trailer"

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="media")
    media_type = models.CharField(max_length=20, choices=MediaType.choices)
    url = models.TextField()
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "game_media"
        ordering = ["sort_order"]


class GameExternalId(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="external_ids")
    source = models.CharField(max_length=30)  # igdb, rawg
    external_id = models.CharField(max_length=100)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "game_external_ids"
        constraints = [
            models.UniqueConstraint(fields=["game", "source"], name="uniq_external_game_source"),
            models.UniqueConstraint(
                fields=["source", "external_id"], name="uniq_external_source_id"
            ),
        ]
