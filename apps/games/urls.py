from django.urls import path
from .views import steam_game_list

urlpatterns = [
    path("steam/", steam_game_list, name="steam-game-list"),
]