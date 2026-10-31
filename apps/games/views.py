from concurrent.futures import ThreadPoolExecutor
import hashlib
import logging
import math
import requests
import re
from apps.core.models import Game, Rating
from apps.games.serializers import (
    get_steam_game_details,
    get_steam_game_list,
    get_steam_game_reviews,
    search_steam_store,
    search_steam_titles,
)
from apps.reviews.serializers import rating_stats_for
from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Count
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 12
MAX_LIMIT = 100

MAX_SUMMARY_IDS = 30

DETAILS_CACHE_SECONDS = 60 * 60  # Steam rate-limits appdetails (~200 / 5 min)
REVIEWS_CACHE_SECONDS = 60 * 10
BROWSE_PAGE_SIZE = 24
POOL_CACHE_SECONDS = 60 * 10

# Slugs must match GENRES in Browse.tsx. Values are Steam store tag IDs.
GENRE_TAGS = {
    "action": 19,
    "adventure": 21,
    "casual": 597,
    "indie": 492,
    "rpg": 122,
    "simulation": 599,
    "strategy": 9,
    "sports": 701,
    "racing": 699,
    "puzzle": 1664,
    "platformer": 1625,
    "roguelike": 1716,
    "horror": 1667,
}

# Frontend platform slug -> Steam `os` param
PLATFORM_OS = {"windows": "win", "mac": "mac", "linux": "linux"}

# Frontend sort slug -> Steam `sort_by` param
SORT_BY = {
    "relevance": "_ASC",
    "rated": "Reviews_DESC",
    "newest": "Released_DESC",
    "price_low": "Price_ASC",
    "price_high": "Price_DESC",
    "name": "Name_ASC",
}

ExternalId = Game._meta.get_field("external_ids").related_model

STEAM_RATING_CACHE_SECONDS = 60 * 60 * 6


def _to_int(value, default, minimum=0):
    try:
        return max(int(value), minimum)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Basic Steam endpoints
# ---------------------------------------------------------------------------
@api_view(["GET"])
def steam_game_list(request):
    limit = min(
        _to_int(request.query_params.get("limit"), DEFAULT_LIMIT, 1),
        MAX_LIMIT,
    )
    last_appid = _to_int(request.query_params.get("last_appid"), 0)

    try:
        result = get_steam_game_list(
            api_key=settings.STEAM_API_KEY,
            last_appid=last_appid,
            limit=limit,
        )
    except Exception as e:
        logger.exception("Failed to fetch steam game list from Steam API")
        return Response(
            {"error": f"Steam API error: {str(e)}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if request.query_params.get("expand") == "1":
        try:
            games_data = result.get("games", []) if isinstance(result, dict) else []
            app_ids = [g["appid"] for g in games_data if isinstance(g, dict) and "appid" in g][:MAX_SUMMARY_IDS]
            summarized_games = _build_summaries(app_ids, request.user)
            if isinstance(result, dict):
                result = {**result, "games": summarized_games}
            else:
                result = {"games": summarized_games, "have_more_results": False, "last_appid": 0}
        except Exception as e:
            logger.exception("Failed building summaries in steam_game_list")
            # Fallback to returning raw games if summary building fails so the page doesn't 500
            pass

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


# ---------------------------------------------------------------------------
# Browse helpers
# ---------------------------------------------------------------------------
def _details_for(app_id):
    """Cached appdetails (same cache key as steam_game_details)."""
    key = f"steam:details:{app_id}"
    game = cache.get(key)
    if game is None:
        try:
            game = get_steam_game_details(app_id)
        except requests.RequestException:
            return None
        if game is not None:
            cache.set(key, game, DETAILS_CACHE_SECONDS)
    return game


def _browse_card(app_id, details):
    platforms = details.get("platforms") or {}
    release_text = (details.get("release_date") or {}).get("date", "")
    year_match = re.search(r"\d{4}", release_text)
    metacritic = (details.get("metacritic") or {}).get("score")
    price = details.get("price_overview") or {}

    return {
        "appid": app_id,
        "name": details.get("name") or "",
        "header_image": details.get("header_image")
        or f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg",
        "genres": [g["description"] for g in details.get("genres", [])],
        "platforms": [
            label
            for key, label in (("windows", "PC"), ("mac", "Mac"), ("linux", "Linux"))
            if platforms.get(key)
        ],
        "release_year": int(year_match.group()) if year_match else None,
        "price": "Free to play" if details.get("is_free") else price.get("final_formatted"),
        "score": round(metacritic / 10, 1) if metacritic else None,
    }


def _external_id_fields():
    """Find the Steam-id table's field names without hardcoding them."""
    model = Game._meta.get_field("external_ids").related_model
    names = {f.name for f in model._meta.get_fields()}

    def pick(*candidates):
        return next((c for c in candidates if c in names), None)

    source = pick("source", "provider", "platform", "store")
    value = pick("external_id", "value", "identifier", "app_id", "steam_id", "key")
    if not value:
        raise RuntimeError(f"Unknown external id fields: {sorted(names)}")
    return model, source, value


def _game_ids_for(app_ids):
    """{our Game id: steam app id} for the games we already know about."""
    model, source_field, value_field = _external_id_fields()

    links = model.objects.filter(**{f"{value_field}__in": [str(a) for a in app_ids]})
    if source_field:
        links = links.filter(**{f"{source_field}__iexact": "steam"})

    return {
        game_id: int(ext) for game_id, ext in links.values_list("game_id", value_field)
    }


def _ratings_for(game_to_app):
    """GameShelf community average + count, one query."""
    if not game_to_app:
        return {}
    rows = (
        Rating.objects.filter(game_id__in=game_to_app.keys())
        .values("game_id")
        .annotate(average=Avg("score"), count=Count("id"))
    )
    return {
        game_to_app[row["game_id"]]: {
            "average": round(float(row["average"]), 1),
            "count": row["count"],
        }
        for row in rows
    }


def _my_scores_for(user, game_to_app):
    """The logged-in user's own scores {steam app id: score}. Empty if anonymous."""
    if user is None or not user.is_authenticated or not game_to_app:
        return {}
    rows = Rating.objects.filter(
        user=user, game_id__in=game_to_app.keys()
    ).values_list("game_id", "score")
    return {game_to_app[game_id]: score for game_id, score in rows}


def _steam_rating_for(app_id):
    """
    Third-party rating: Steam's positive-review share, scaled to 0-10.
    Cached server-side for 6 hours.
    """
    key = f"steam:rating:{app_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached or None

    try:
        data = get_steam_game_reviews(app_id, num_per_page=1)
    except requests.RequestException:
        return None

    summary = data.get("summary") or {}
    total = summary.get("total_reviews") or 0
    positive = summary.get("total_positive") or 0

    result = (
        {
            "score": round(positive / total * 10, 1),
            "label": summary.get("review_score_desc") or "",
            "total_reviews": total,
        }
        if total
        else {}
    )
    cache.set(key, result, STEAM_RATING_CACHE_SECONDS)
    return result or None


def _summary_card(app_id, details, rating, steam_rating):
    card = _browse_card(app_id, details)
    card.update(
        {
            "short_description": details.get("short_description") or "",
            "metacritic_score": (details.get("metacritic") or {}).get("score"),
            "steam_rating": steam_rating,
            "community_rating": rating["average"],
            "rating_count": rating["count"],
            "my_score": rating["my_score"],
        }
    )
    return card


def _build_summaries(app_ids, user=None):
    app_ids = list(dict.fromkeys(app_ids))

    with ThreadPoolExecutor(max_workers=8) as executor:
        details = list(executor.map(_details_for, app_ids))
        steam_ratings = list(executor.map(_steam_rating_for, app_ids))

    try:
        stats = rating_stats_for(app_ids, user)
    except Exception:
        logger.exception("rating lookup failed")
        stats = {}

    empty = {"my_score": None, "average": None, "count": 0}

    return [
        _summary_card(app_id, d, stats.get(app_id, empty), steam)
        for app_id, d, steam in zip(app_ids, details, steam_ratings)
        if d and d.get("type") == "game"
    ]


def _search_pool(term, tag_id, platform_os, sort_by):
    key = "steam:search-pool:v2:" + hashlib.md5(
        f"{term.lower()}|{tag_id}|{platform_os}|{sort_by}".encode()
    ).hexdigest()

    pool = cache.get(key)
    if pool is None:
        pool = search_steam_titles(term=term, tag_id=tag_id, platform=platform_os)
        if sort_by == SORT_BY["name"]:
            pool.sort(key=lambda r: r["name"].lower())
        cache.set(key, pool, POOL_CACHE_SECONDS)
    return pool


def _store_search_page(term, tag_id, platform_os, sort_by, page):
    start = (page - 1) * BROWSE_PAGE_SIZE

    key = "steam:browse:v2:" + hashlib.md5(
        f"{term.lower()}|{tag_id}|{platform_os}|{sort_by}|{start}".encode()
    ).hexdigest()

    cached = cache.get(key)
    if cached is not None:
        return cached

    if term:
        pool = _search_pool(term, tag_id, platform_os, sort_by)
        total = len(pool)
        appids = [r["appid"] for r in pool[start : start + BROWSE_PAGE_SIZE]]
    else:
        appids, total = search_steam_store(
            term="",
            tag_id=tag_id,
            sort_by=sort_by,
            platform=platform_os,
            start=start,
            count=BROWSE_PAGE_SIZE,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        details = list(executor.map(_details_for, appids))

    cards = [
        _browse_card(app_id, d)
        for app_id, d in zip(appids, details)
        if d and d.get("type") == "game"
    ]

    result = (cards, total)
    if all(details):
        cache.set(key, result, POOL_CACHE_SECONDS)
    return result


# ---------------------------------------------------------------------------
# Browse endpoint
# ---------------------------------------------------------------------------
@api_view(["GET"])
def steam_game_browse(request):
    """GET /games/steam/browse/?q=&genre=&platform=&year=&sort=&page=1"""
    params = request.query_params
    term = (params.get("q") or "").strip()[:100]
    tag_id = GENRE_TAGS.get(params.get("genre", ""))
    platform_os = PLATFORM_OS.get(params.get("platform", ""))
    year = _to_int(params.get("year"), 0)
    sort_by = SORT_BY.get(params.get("sort", "rated"), SORT_BY["rated"])
    page = _to_int(params.get("page"), 1, 1)

    try:
        cards, total = _store_search_page(term, tag_id, platform_os, sort_by, page)
    except (requests.RequestException, ValueError):
        return Response(
            {"error": "Steam search isn't responding right now."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if year:
        cards = [c for c in cards if c["release_year"] == year]

    total_pages = max(1, math.ceil(total / BROWSE_PAGE_SIZE))

    return Response(
        {
            "count": total,
            "page": page,
            "page_size": BROWSE_PAGE_SIZE,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "endless": False,
            "results": cards,
        }
    )


@api_view(["GET"])
def steam_game_summaries(request):
    raw = request.query_params.get("appids", "")
    app_ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
    app_ids = app_ids[:MAX_SUMMARY_IDS]

    if not app_ids:
        return Response({"results": []})

    try:
        results = _build_summaries(app_ids, request.user)
    except requests.RequestException:
        return Response(
            {"error": "Unable to fetch game details from Steam."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response({"results": results})