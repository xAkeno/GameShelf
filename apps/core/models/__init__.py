# Django discovers models through this file, so every model must be imported here.
# Existing imports such as `from core.models import Game` keep working.
from .base import SoftDeleteModel, TimeStampedModel  # noqa: F401
from .users import (
    Friendship,
    Notification,
    SanctionAppeal,
    User,
    UserFavoriteGame,
    UserPlatform,
    UserSanction,
)
from .games import (  # noqa: F401
    Award, Developer, Game, GameAward, GameDeveloper, GameExternalId, GameGenre,
    GameLanguage, GameMedia, GamePlatform, GamePublisher, GameTag, Genre, Language,
    Platform, Publisher, SystemRequirement, Tag,
)
from .library import (  # noqa: F401
    GameLike, Playlist, PlaylistGame, PlaylistLike, UserDismissedGame, UserGameList,
    UserGenre,
)
from .reviews import (  # noqa: F401
    CommentAttachment, CommentLike, Rating, Review, ReviewComment, ReviewLike,
)
from .moderation import (  # noqa: F401
    CommentReport, ModerationAction, ModerationFlag, PlaylistReport, ReviewReport,
)
from .recommendations import (  # noqa: F401
    Cluster, ClusterMember, GameActivityDaily, GameScore, GameSimilarity, ModelRun,
    RecommendationEvent, UserRecommendation,
)
from .system import AdminAuditLog, DailySiteStat, SecurityEvent, SiteSetting  # noqa: F401
