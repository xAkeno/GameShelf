# Django discovers models through this file, so every model must be imported here.
# Existing imports such as `from core.models import Game` keep working.
from .base import SoftDeleteModel, TimeStampedModel  # noqa: F401
from .users import (  # noqa: F401
    Friendship, Notification, SanctionAppeal, User, UserFavoriteGame, UserSanction,
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


# gameshelf/                      # main folder
# ├── manage.py
# ├── config/
# │   ├── settings/
# │   │   ├── base.py
# │   │   ├── dev.py
# │   │   └── prod.py
# │   ├── urls.py                 # includes each app's urls
# │   ├── asgi.py / wsgi.py
# │   └── celery.py               # if you use background jobs
# └── apps/
#     ├── core/                   # shared helpers only, no business models
#     │   ├── models.py           # abstract TimeStampedModel, SoftDeleteModel
#     │   ├── permissions.py      # IsModerator, IsAdmin, IsOwner
#     │   ├── pagination.py
#     │   ├── validators.py       # no-links validator, image/gif upload checks
#     │   └── utils.py
#     ├── users/                  # users, friendships, sanctions, appeals
#     ├── games/                  # games, developers, publishers, platforms, genres,
#     │                           # languages, awards, system_requirements, game_media,
#     │                           # game_external_ids
#     ├── library/                # user_game_lists, playlists, playlist_games,
#     │                           # user_genres, game_likes
#     ├── reviews/                # ratings, reviews, review_comments,
#     │                           # comment_attachments, likes
#     ├── social/                 # notifications (and friendships if you split it out)
#     ├── moderation/             # reports, flags, actions, admin_audit_log
#     ├── recommendations/        # recommendation_events + the algorithms
#     └── analytics/              # daily_site_stats + the nightly job