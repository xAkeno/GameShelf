import requests
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, IntegerField, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.models import Game, GameExternalId, UserGameList

# If the import above fails, GameExternalId/Game aren't exported in
# apps/core/models/__init__.py. Use the explicit paths instead:
# from apps.core.models.games import Game, GameExternalId
# from apps.core.models.library import UserGameList
from apps.games.serializers import get_steam_game_details

from .serializers import (
    AddToListSerializer,
    UpdateListEntrySerializer,
    UserGameListSerializer,
    CreatePlaylistSerializer,
    PlaylistGameSearchSerializer,
)

Status = UserGameList.Status
STEAM = "steam"

STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"


def _steam_search(term):
    key = f"steam:search:{term.lower()}"
    items = cache.get(key)
    if items is None:
        resp = requests.get(
            STEAM_SEARCH_URL,
            params={"term": term, "l": "english", "cc": "us"},
            timeout=5,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        cache.set(key, items, 300)
    return items

def _entries_for(user):
    return (
        UserGameList.objects.filter(user=user)
        .select_related("game")
        .prefetch_related("game__external_ids", "game__genres")
    )


def _apply_status(entry, new_status):
    """Set the status and keep started_at / completed_at sensible."""
    today = timezone.localdate()
    entry.status = new_status

    if new_status == Status.PLAYING and entry.started_at is None:
        entry.started_at = today

    if new_status == Status.COMPLETED:
        entry.completed_at = entry.completed_at or today
    else:
        entry.completed_at = None


def _find_game(app_id):
    """Local Game for a Steam app id, or None."""
    return Game.objects.filter(
        external_ids__source=STEAM,
        external_ids__external_id=str(app_id),
    ).first()


def _get_or_create_game(app_id):
    """
    Find the local Game for a Steam app id, creating it from Steam's data the
    first time someone adds it. Returns None if Steam doesn't know the app.
    May raise requests.RequestException.
    """
    game = _find_game(app_id)
    if game:
        return game

    details = cache.get(f"steam:details:{app_id}") or get_steam_game_details(app_id)
    if not details:
        return None

    name = details.get("name") or f"Steam app {app_id}"

    # The DB constraint requires every DLC to have a parent game. Use Steam's
    # "fullgame" info if we already have that base game, otherwise store the
    # entry as a base game so the add doesn't fail.
    game_type, parent = Game.GameType.BASE, None
    if details.get("type") == "dlc":
        try:
            parent = _find_game(int((details.get("fullgame") or {}).get("appid")))
        except (TypeError, ValueError):
            parent = None
        if parent:
            game_type = Game.GameType.DLC

    try:
        with transaction.atomic():
            game = Game.objects.create(
                title=name,
                slug=f"{slugify(name) or 'app'}-{app_id}",
                cover_image_url=details.get("header_image"),
                game_type=game_type,
                parent_game=parent,
                source=Game.Source.API,
            )
            GameExternalId.objects.create(
                game=game,
                source=STEAM,
                external_id=str(app_id),
                last_synced_at=timezone.now(),
            )
    except IntegrityError:
        # Another request created it first.
        game = _find_game(app_id)
        if game is None:
            raise
    return game


def _add_to_list(request):
    serializer = AddToListSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    app_id = serializer.validated_data["steam_app_id"]
    new_status = serializer.validated_data["status"]

    try:
        game = _get_or_create_game(app_id)
    except requests.RequestException:
        return Response(
            {"error": "Unable to fetch game details from Steam."},
            status=http.HTTP_502_BAD_GATEWAY,
        )

    if game is None:
        return Response(
            {"error": f"Game not found on Steam (app id {app_id})."},
            status=http.HTTP_404_NOT_FOUND,
        )

    entry, created = UserGameList.objects.get_or_create(user=request.user, game=game)
    _apply_status(entry, new_status)
    entry.save()

    # Re-fetch so select/prefetch is applied for the serializer.
    entry = _entries_for(request.user).get(pk=entry.pk)

    return Response(
        UserGameListSerializer(entry).data,
        status=http.HTTP_201_CREATED if created else http.HTTP_200_OK,
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def user_game_list(request):
    """
    GET  /list/?status=playing&search=hollow
         -> { counts: {...}, results: [...] }
    POST /list/  { steam_app_id, status }
         -> create the entry, or change its status if it already exists
    """
    if request.method == "POST":
        return _add_to_list(request)

    base = _entries_for(request.user)

    # Counts always describe the whole list, regardless of filters.
    raw = {
        row["status"]: row["n"]
        for row in base.order_by().values("status").annotate(n=Count("id"))
    }
    counts = {value: raw.get(value, 0) for value in Status.values}
    counts["all"] = sum(counts.values())

    queryset = base
    status_filter = request.query_params.get("status")
    if status_filter in Status.values:
        queryset = queryset.filter(status=status_filter)

    search = (request.query_params.get("search") or "").strip()
    if search:
        queryset = queryset.filter(game__title__icontains=search)

    results = UserGameListSerializer(queryset.order_by("-id"), many=True).data
    return Response({"counts": counts, "results": results})


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def user_game_list_entry(request, entry_id):
    """PATCH /list/<id>/ (change status etc.)  |  DELETE (remove)."""
    entry = get_object_or_404(_entries_for(request.user), pk=entry_id)

    if request.method == "DELETE":
        entry.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)

    serializer = UpdateListEntrySerializer(entry, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)

    new_status = serializer.validated_data.get("status")
    entry = serializer.save()
    if new_status:
        _apply_status(entry, new_status)
        entry.save()

    return Response(UserGameListSerializer(entry).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def user_game_list_status(request, app_id):
    """GET /list/steam/<app_id>/  -> is this game in my list, and as what?"""
    entry = (
        _entries_for(request.user)
        .filter(
            game__external_ids__source=STEAM,
            game__external_ids__external_id=str(app_id),
        )
        .first()
    )

    if entry is None:
        return Response({"in_list": False})

    return Response({"in_list": True, **UserGameListSerializer(entry).data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_playlist(request):
    serializer = CreatePlaylistSerializer(data=request.data, context={"request": request})
    serializer.is_valid(raise_exception=True)

    games = []
    try:
        for app_id in serializer.validated_data["steam_app_ids"]:
            game = _get_or_create_game(app_id)
            if game is None:
                return Response(
                    {"steam_app_ids": [f"Game not found on Steam (app id {app_id})."]},
                    status=http.HTTP_400_BAD_REQUEST,
                )
            games.append(game)
    except requests.RequestException:
        return Response(
            {"error": "Unable to fetch game details from Steam."},
            status=http.HTTP_502_BAD_GATEWAY,
        )

    try:
        with transaction.atomic():
            playlist = serializer.save(games=games)
    except IntegrityError:
        return Response(
            {"error": "A playlist with this title already exists."},
            status=http.HTTP_409_CONFLICT,
        )

    return Response(
        {"id": playlist.id, "title": playlist.title, "visibility": playlist.visibility},
        status=http.HTTP_201_CREATED,
    )

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def search_playlist_games(request):
    """GET /playlists/games/?search=<title> -> [{appid, title, cover}]"""
    search = (request.query_params.get("search") or "").strip()
    if len(search) < 2:
        return Response({"results": []})

    try:
        limit = min(max(int(request.query_params.get("limit", 20)), 1), 50)
    except (TypeError, ValueError):
        limit = 20

    try:
        items = _steam_search(search)
        results = [
            {
                "appid": item["id"],
                "title": item["name"],
                "cover": f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{item['id']}/header.jpg",
            }
            for item in items
            if item.get("type") in (None, "app") and item.get("id") and item.get("name")
        ][:limit]
        return Response({"results": results})
    except (requests.RequestException, ValueError):
        pass

    # Fallback: games already in our DB
    games = (
        Game.objects.filter(title__icontains=search, external_ids__source=STEAM)
        .prefetch_related("external_ids")
        .distinct()[:limit]
    )
    results = []
    for g in games:
        ext = next((e for e in g.external_ids.all() if e.source == STEAM), None)
        if ext and str(ext.external_id).isdigit():
            results.append({"appid": int(ext.external_id), "title": g.title, "cover": g.cover_image_url or ""})
    return Response({"results": results})