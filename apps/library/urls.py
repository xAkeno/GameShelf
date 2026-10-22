from django.urls import path

from .views import (
    create_playlist,
    search_playlist_games,
    user_game_list,
    user_game_list_entry,
    user_game_list_status,
)

urlpatterns = [
    path(
        "playlists/games/",
        search_playlist_games,
        name="search-playlist-games",
    ),
    path(
        "playlists/",
        create_playlist,
        name="create-playlist",
    ),
    # The signed-in user's list
    path("", user_game_list, name="user-game-list"),
    path(
        "<int:entry_id>/",
        user_game_list_entry,
        name="user-game-list-entry",
    ),
    path(
        "steam/<int:app_id>/",
        user_game_list_status,
        name="user-game-list-status",
    ),
]