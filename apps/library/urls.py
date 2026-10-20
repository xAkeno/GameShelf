from django.urls import path

from .views import (
    user_game_list,
    user_game_list_entry,
    user_game_list_status,
)

urlpatterns = [
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