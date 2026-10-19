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
        limit = int_param(request, "limit", 12, 1, 50)
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
        game = get_object_or_404(Game, pk=pk)
        limit = int_param(request, "limit", 12, 1, 50)
        run = active_run("tfidf")
        if not run:
            return Response({"results": []})
        rows = (
            GameSimilarity.objects.filter(model_run=run, game=game)
            .select_related("similar_game")
            .prefetch_related("similar_game__genres")
            .order_by("rank")[:limit]
        )
        return Response({"results": SimilarGameSerializer(rows, many=True).data})


class TopGamesViewSet(viewsets.ViewSet):
    """GET /top-games/?ordering=rating|popularity|trending&limit=20&offset=0"""

    permission_classes = [permissions.AllowAny]
    FIELDS = {"rating": "bayesian_score", "popularity": "popularity_score", "trending": "trending_score"}

    def list(self, request):
        ordering = request.query_params.get("ordering", "rating")
        field = self.FIELDS.get(ordering, "bayesian_score")
        limit = int_param(request, "limit", 20, 1, 100)
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

    def list(self, request):
        clusters = Cluster.objects.filter(model_run__is_active=True, entity_type="game").order_by("-size")
        return Response({"results": ClusterSerializer(clusters, many=True).data})

    def retrieve(self, request, pk=None):
        cluster = get_object_or_404(Cluster, pk=pk, model_run__is_active=True, entity_type="game")
        limit = int_param(request, "limit", 24, 1, 100)
        members = (
            ClusterMember.objects.filter(cluster=cluster, game__isnull=False)
            .select_related("game")
            .prefetch_related("game__genres")
            .order_by("distance")[:limit]
        )
        data = ClusterSerializer(cluster).data
        data["games"] = ClusterMemberSerializer(members, many=True).data
        return Response(data)
