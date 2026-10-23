from django.db.models import F
from rest_framework import permissions, viewsets
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from apps.core.models import (
    Cluster,
    ClusterMember,
    Game,
    GameScore,
    GameSimilarity,
    ModelRun,
    UserDismissedGame,
    UserGameList,
    UserRecommendation,
)

from .serializers import (
    ClusterMemberSerializer,
    ClusterSerializer,
    GameBriefSerializer,
    GameScoreSerializer,
    RecommendationSerializer,
    SimilarGameSerializer,
)
import json

from functools import lru_cache
from pathlib import Path

from .jobs import _load_steam_games, RECOMMENDATION_DIR

@lru_cache(maxsize=1)
def steam_games():
    return _load_steam_games()


def load_recommendation_artifact(filename):
    path = RECOMMENDATION_DIR / filename

    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


def steam_game_payload(appid):
    game = steam_games().get(int(appid))

    if not game:
        return None

    return {
        "id": int(appid),
        "appid": int(appid),
        "title": game["name"],
        "slug": None,
        "cover_image_url": None,
        "release_date": game["release_date"],
        "genres": game["genres"],
    }

def active_run(algorithm: str):
    return ModelRun.objects.filter(algorithm=algorithm, is_active=True).first()


def int_param(request, name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(request.query_params.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


class RecommendationViewSet(viewsets.ViewSet):
    """GET /recommendations/ : personalised picks for a logged-in player who has some,
    otherwise popular games. `source` says which one you got, so the page never claims
    "because you played ..." for someone with no history."""

    permission_classes = [permissions.AllowAny]

    def list(self, request):
        try:
            limit = int(request.query_params.get("limit", 10))
        except (TypeError, ValueError):
            limit = 10

        limit = max(1, min(limit, 50))
        user = request.user

        if user.is_authenticated:
            run = active_run("svd")
            if run:
                # the player may have added, completed or dismissed games since the last run
                hidden = set(UserGameList.objects.filter(user=user).values_list("game_id", flat=True))
                hidden |= set(UserDismissedGame.objects.filter(user=user).values_list("game_id", flat=True))
                picks = list(
                    UserRecommendation.objects.filter(model_run=run, user=user)
                    .exclude(game_id__in=hidden)
                    .select_related("game", "reason_game")
                    .prefetch_related("game__genres")
                    .order_by("rank")[:limit]
                )
                if picks:
                    return Response(
                        {"source": "personalized", "results": RecommendationSerializer(picks, many=True).data}
                    )

        scores = list(
            GameScore.objects.select_related("game")
            .prefetch_related("game__genres")
            .order_by(F("bayesian_score").desc(nulls_last=True), "-popularity_score", "game_id")[:limit]
        )
        games = [s.game for s in scores] or list(
            Game.objects.prefetch_related("genres").order_by("-id")[:limit]
        )
        by_game = {s.game_id: s for s in scores}
        results = [
            {
                "game": GameBriefSerializer(game).data,
                "score": float(by_game[game.id].bayesian_score) if game.id in by_game and by_game[game.id].bayesian_score is not None else None,
                "rank": rank,
                "reason_type": "popular",
                "reason_text": "Popular with GameShelf players",
                "reason_game": None,
            }
            for rank, game in enumerate(games, start=1)
        ]
        return Response({"source": "popular", "results": results})


class SimilarGamesViewSet(viewsets.ViewSet):
    """GET /similar/<game_id>/ : the "Similar titles" row on a game page."""

    permission_classes = [permissions.AllowAny]

    def retrieve(self, request, pk=None):
        try:
            appid = int(pk)
        except (TypeError, ValueError):
            return Response(
                {"results": [], "detail": "Invalid Steam app ID."},
                status=400,
            )

        artifact = load_recommendation_artifact("tfidf_similarities.json")

        if not artifact:
            return Response({"results": []})

        limit = int_param(request, "limit", default=10, low=1, high=50)
        similarities = artifact.get("games", {}).get(str(appid), [])

        results = []

        for item in similarities[:limit]:
            game = steam_game_payload(item["appid"])

            if game is None:
                continue

            results.append({
                "game": game,
                "score": item["score"],
                "rank": item["rank"],
                "reason": item.get("reason", {}),
            })

        return Response({"results": results})


class TopGamesViewSet(viewsets.ViewSet):
    """GET /top-games/?ordering=rating|popularity|trending&limit=20&offset=0"""

    permission_classes = [permissions.AllowAny]
    FIELDS = {"rating": "bayesian_score", "popularity": "popularity_score", "trending": "trending_score"}

    def list(self, request):
        ordering = request.query_params.get("ordering", "rating")
        field = self.FIELDS.get(ordering, "bayesian_score")
        limit = int_param(request, "limit", default=10, low=1, high=50)
        offset = int_param(request, "offset", 0, 0, 1_000_000)

        queryset = GameScore.objects.select_related("game").prefetch_related("game__genres")
        if field == "bayesian_score":
            queryset = queryset.filter(bayesian_score__isnull=False)  # unrated games aren't "top rated"
        queryset = queryset.order_by(F(field).desc(nulls_last=True), "game_id")
        return Response(
            {
                "count": queryset.count(),
                "results": GameScoreSerializer(queryset[offset:offset + limit], many=True).data,
            }
        )


class ClusterViewSet(viewsets.ViewSet):
    """GET /clusters/ lists the groups of similar games, /clusters/<id>/ shows one with its games."""

    permission_classes = [permissions.AllowAny]

    def list(self, request, *args, **kwargs):
        artifact = load_recommendation_artifact("game_clusters.json")

        if not artifact:
            return Response([])

        results = [
            {
                "id": cluster["id"],
                "label": cluster["label"],
                "size": cluster["size"],
            }
            for cluster in artifact.get("clusters", [])
        ]

        return Response(results)


    def retrieve(self, request, pk=None, *args, **kwargs):
        try:
            cluster_id = int(pk)
        except (TypeError, ValueError):
            return Response(
                {"detail": "Invalid cluster ID."},
                status=400,
            )

        artifact = load_recommendation_artifact("game_clusters.json")

        if not artifact:
            return Response(
                {"detail": "Game clusters have not been generated yet."},
                status=404,
            )

        cluster = next(
            (
                item
                for item in artifact.get("clusters", [])
                if item["id"] == cluster_id
            ),
            None,
        )

        if cluster is None:
            return Response(
                {"detail": "Cluster not found."},
                status=404,
            )

        games = []

        for member in cluster.get("games", []):
            game = steam_game_payload(member["appid"])

            if game is None:
                continue

            games.append({
                "game": game,
                "distance": member["distance"],
            })

        return Response({
            "id": cluster["id"],
            "label": cluster["label"],
            "size": cluster["size"],
            "games": games,
        })
