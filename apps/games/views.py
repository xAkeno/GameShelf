import requests
from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.games.serializers import (
    get_steam_game_details,
    get_steam_game_list,
    get_steam_game_reviews,
)

DEFAULT_LIMIT = 12
MAX_LIMIT = 100

DETAILS_CACHE_SECONDS = 60 * 60       # Steam rate-limits appdetails (~200 / 5 min)
REVIEWS_CACHE_SECONDS = 60 * 10


def _to_int(value, default, minimum=0):
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


@api_view(["GET"])
def steam_game_list(request):
    limit = min(
        _to_int(request.query_params.get("limit"), DEFAULT_LIMIT, 1),
        MAX_LIMIT,
    )
    last_appid = _to_int(request.query_params.get("last_appid"), 0)

    result = get_steam_game_list(
        api_key=settings.STEAM_API_KEY,
        last_appid=last_appid,
        limit=limit,
    )

    return Response(result)


@api_view(["GET"])
def steam_game_details(request, app_id):
    cache_key = f"steam:details:{app_id}"
    game = cache.get(cache_key)

    if game is None:
        try:
            game = get_steam_game_details(app_id)
        except requests.RequestException:
            return Response(
                {"error": "Unable to fetch game details from Steam."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if game is None:
            return Response(
                {"error": "Game not found on Steam."},
                status=status.HTTP_404_NOT_FOUND,
            )

        cache.set(cache_key, game, DETAILS_CACHE_SECONDS)

    return Response(game, status=status.HTTP_200_OK)


@api_view(["GET"])
def steam_game_reviews(request, app_id):
    num_per_page = min(
        _to_int(request.query_params.get("num_per_page"), 5, 1),
        20,
    )
    cursor = request.query_params.get("cursor", "*")
    language = request.query_params.get("language", "english")

    cache_key = f"steam:reviews:{app_id}:{num_per_page}:{language}:{cursor}"
    result = cache.get(cache_key)

    if result is None:
        try:
            result = get_steam_game_reviews(
                app_id,
                num_per_page=num_per_page,
                cursor=cursor,
                language=language,
            )
        except requests.RequestException:
            return Response(
                {"error": "Unable to fetch reviews from Steam."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        cache.set(cache_key, result, REVIEWS_CACHE_SECONDS)

    return Response(result, status=status.HTTP_200_OK)