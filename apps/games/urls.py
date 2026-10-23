from django.urls import path

from .views import (
    steam_game_browse,
    steam_game_details,
    steam_game_list,
    steam_game_reviews,
    steam_game_summaries,
)

urlpatterns = [
    path("steam/", steam_game_list, name="steam-game-list"),
    path("steam/browse/", steam_game_browse, name="steam-game-browse"),
    path("steam/summaries/", steam_game_summaries, name="steam-game-summaries"),
    path("steam/<int:app_id>/", steam_game_details, name="steam-game-details"),
    path(
        "steam/<int:app_id>/reviews/",
        steam_game_reviews,
        name="steam-game-reviews",
    ),
]