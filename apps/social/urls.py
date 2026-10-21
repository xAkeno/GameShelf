from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    BlockViewSet,
    CommunityPlaylistViewSet,
    NotificationViewSet,
    FriendViewSet,
    FriendRequestViewSet,
    SuggestedUserViewSet,
)

router = DefaultRouter()

router.register("blocks", BlockViewSet, basename="blocks")
router.register("friends", FriendViewSet, basename="friends")
router.register(
    "friend-requests",
    FriendRequestViewSet,
    basename="friend-requests",
)
router.register(
    "suggested-players",
    SuggestedUserViewSet,
    basename="suggested-player",
)
router.register(
    "notifications",
    NotificationViewSet,
    basename="notifications",
)
router.register(
    "playlists",
    CommunityPlaylistViewSet,
    basename="community-playlists",
)

urlpatterns = [
    path("", include(router.urls)),
]