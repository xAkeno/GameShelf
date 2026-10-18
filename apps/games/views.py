from django.conf import settings
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.games.serializers import get_steam_game_list

DEFAULT_LIMIT = 12
MAX_LIMIT = 100


def _to_int(value, default, minimum=0):
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


@api_view(["GET"])
def steam_game_list(request):
    limit = min(_to_int(request.query_params.get("limit"), DEFAULT_LIMIT, 1), MAX_LIMIT)
    last_appid = _to_int(request.query_params.get("last_appid"), 0)

    result = get_steam_game_list(
        api_key=settings.STEAM_API_KEY,
        last_appid=last_appid,
        limit=limit,
    )

    return Response(result)