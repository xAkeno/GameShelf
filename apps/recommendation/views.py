from django.db.models import F
from rest_framework import permissions, viewsets
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

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
    Genre,
    GameExternalId,
    UserFavoriteGame,
    UserGenre,
    UserPlatform,
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
import numpy as np
from collections import Counter
from functools import lru_cache
from pathlib import Path

from . import algorithms
from .jobs import _load_steam_games, _write_json_atomic, RECOMMENDATION_DIR

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
        "header_image": game.get("header_image"),
        "release_date": game["release_date"],
        "genres": game["genres"],
    }

def active_run(algorithm: str):
    return ModelRun.objects.filter(algorithm=algorithm, is_active=True).first()


CLUSTERS_FILE = "game_clusters.json"
PROFILE_DIR = RECOMMENDATION_DIR / "taste_profiles"
PLATFORMS = [("windows", "Windows"), ("mac", "macOS"), ("linux", "Linux")]
MIN_TAG_GAMES = 20       # a tag must describe at least this many games to be offered
MAX_TAG_OPTIONS = 300
MAX_PICKED_GENRES = 40
MAX_PICKED_TAGS = 30
MAX_LIKED_GAMES = 20


@lru_cache(maxsize=1)
def steam_title_map():
    title_map = {}
    for appid, game in steam_games().items():
        title_map.setdefault(game["name"].strip().casefold(), appid)
    return title_map


@lru_cache(maxsize=1)
def steam_interest_options():
    """Every genre and the most used tags in the Steam data, most common first."""
    genre_counts, tag_counts = Counter(), Counter()
    for game in steam_games().values():
        genre_counts.update(game["genres"])
        tag_counts.update(game["tags"][: algorithms.MAX_TAGS_PER_GAME])

    def ranked(counts, minimum):
        return [
            {"name": name, "count": count}
            for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
            if count >= minimum
        ]

    return {
        "genres": ranked(genre_counts, 1),
        "tags": ranked(tag_counts, MIN_TAG_GAMES)[:MAX_TAG_OPTIONS],
    }


def clean_names(value, label, limit):
    if not isinstance(value, list) or not all(isinstance(n, str) and n.strip() for n in value):
        return None, f"{label} must be a list of non-empty names."
    unique = {}
    for name in value:
        unique.setdefault(name.strip().casefold(), name.strip())
    if len(unique) > limit:
        return None, f"Pick at most {limit} {label}."
    return list(unique.values()), None


# Tags and liked Steam games live in a small JSON file per user (your tables can't hold them:
# UserFavoriteGame points at your own Game table, but the picker sends Steam app IDs).
def profile_path(user):
    return PROFILE_DIR / f"{user.pk}.json"


def read_profile(user):
    try:
        with profile_path(user).open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "tags": [t for t in data.get("tags", []) if isinstance(t, str)],
        "liked_game_ids": [i for i in data.get("liked_game_ids", []) if isinstance(i, int)],
    }


@lru_cache(maxsize=1)
def _taste_model(mtime_ns):  # mtime_ns only exists so the cache refreshes when the file is rebuilt
    artifact = load_recommendation_artifact(CLUSTERS_FILE)
    if not artifact or "vocabulary" not in artifact:
        raise ValueError("clusters missing or built by an older version")

    games = steam_games()
    vocabulary = artifact["vocabulary"]
    index = {term: i for i, term in enumerate(vocabulary)}
    idf = np.asarray(artifact["idf"], dtype=float)

    appids, labels, names = [], [], {}
    for cluster in artifact["clusters"]:
        names[cluster["id"]] = cluster["label"]
        for member in cluster["games"]:
            appid = int(member["appid"])
            if appid in games:
                appids.append(appid)
                labels.append(cluster["id"])
    if not appids:
        raise ValueError("no clustered games")

    documents = [
        algorithms.kmeans_document(games[a]["genres"], games[a]["tags"]).split() for a in appids
    ]
    matrix = algorithms.term_matrix(documents, index, idf)

    centroids = np.zeros((max(names) + 1, len(vocabulary)))
    for cluster in artifact["clusters"]:
        for term, weight in cluster["centroid"].items():
            if term in index:
                centroids[cluster["id"], index[term]] = weight
    norms = np.linalg.norm(centroids, axis=1, keepdims=True)
    norms[norms == 0] = 1
    centroids = centroids / norms

    votes = np.array([games[a].get("votes", 0) for a in appids], dtype=float)
    quality = np.log1p(votes) / np.log1p(votes.max()) if votes.max() > 0 else np.zeros(len(appids))

    return algorithms.TasteIndex(
        appids=appids,
        row_of={a: i for i, a in enumerate(appids)},
        labels=np.asarray(labels),
        matrix=matrix,
        centroids=centroids,
        cluster_names=names,
        index=index,
        idf=idf,
        quality=quality,
        platform_rows={p: np.array([p in games[a]["platforms"] for a in appids]) for p, _ in PLATFORMS},
    )


def load_taste_model():
    try:
        mtime = (RECOMMENDATION_DIR / CLUSTERS_FILE).stat().st_mtime_ns
        return _taste_model(mtime)
    except (OSError, ValueError, KeyError, TypeError):
        return None


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


class TasteRecommendationViewSet(viewsets.ViewSet):
    """
    GET /taste/           K-Means recommendations from the saved interests.
    GET /taste/options/   Genres, tags and platforms to pick from (taken from the Steam data).
    GET /taste/profile/   Read the saved interests.
    PUT /taste/profile/   Save genres, tags, platforms and liked games.
    """

    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="options", url_name="options")
    def interest_options(self, request):
        data = steam_interest_options()
        return Response({
            "genres": data["genres"],
            "tags": data["tags"],
            "platforms": [{"value": v, "label": label} for v, label in PLATFORMS],
        })

    @action(detail=False, methods=["get", "put"], url_path="profile", url_name="profile")
    def profile_action(self, request):
        if request.method == "GET":
            return self._get_profile(request.user)
        return self._save_profile(request)

    def _get_profile(self, user):
        stored = read_profile(user)
        games = steam_games()
        liked_games = [
            {"appid": a, "title": games[a]["name"], "cover": games[a].get("header_image")}
            for a in stored["liked_game_ids"]
            if a in games
        ]
        return Response({
            "genres": list(
                UserGenre.objects.filter(user=user).select_related("genre")
                .values_list("genre__name", flat=True)
            ),
            "platforms": list(UserPlatform.objects.filter(user=user).values_list("platform", flat=True)),
            "tags": stored["tags"],
            "liked_game_ids": [g["appid"] for g in liked_games],
            "liked_games": liked_games,
        })

    def _save_profile(self, request):
        user = request.user
        bad = lambda message: Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)

        genres, error = clean_names(request.data.get("genres", []), "genres", MAX_PICKED_GENRES)
        if error:
            return bad(error)

        tags = None
        if "tags" in request.data:
            tags, error = clean_names(request.data["tags"], "tags", MAX_PICKED_TAGS)
            if error:
                return bad(error)

        platforms = request.data.get("platforms", [])
        allowed_platforms = {choice.value for choice in UserPlatform.PlatformType}
        if not isinstance(platforms, list) or any(
            not isinstance(p, str) or p not in allowed_platforms for p in platforms
        ):
            return bad("Platforms must be windows, mac, or linux.")
        platforms = list(dict.fromkeys(platforms))

        liked_ids = None
        if "liked_game_ids" in request.data:
            raw = request.data["liked_game_ids"]
            if (
                not isinstance(raw, list)
                or len(raw) > MAX_LIKED_GAMES
                or any(not isinstance(i, int) or isinstance(i, bool) for i in raw)
            ):
                return bad(f"liked_game_ids must be a list of up to {MAX_LIKED_GAMES} Steam app IDs.")
            liked_ids = list(dict.fromkeys(raw))

        with transaction.atomic():
            UserGenre.objects.filter(user=user).delete()
            UserGenre.objects.bulk_create([
                UserGenre(user=user, genre=Genre.objects.get_or_create(name=name)[0])
                for name in genres
            ])
            UserPlatform.objects.filter(user=user).delete()
            UserPlatform.objects.bulk_create([UserPlatform(user=user, platform=p) for p in platforms])

            if tags is not None or liked_ids is not None:  # last step: a failed write rolls the rest back
                stored = read_profile(user)
                if tags is not None:
                    stored["tags"] = tags
                if liked_ids is not None:
                    stored["liked_game_ids"] = liked_ids
                _write_json_atomic(profile_path(user), stored)

        stored = read_profile(user)
        return Response({
            "genres": genres,
            "tags": stored["tags"],
            "platforms": platforms,
            "liked_game_ids": stored["liked_game_ids"],
        })

    def _library_appids(self, user):
        """Steam app IDs of games the player already has in a list or dismissed."""
        game_ids = set(UserGameList.objects.filter(user=user).values_list("game_id", flat=True))
        game_ids |= set(UserDismissedGame.objects.filter(user=user).values_list("game_id", flat=True))
        if not game_ids:
            return set()

        steam_ids = {}
        for game_id, external_id in GameExternalId.objects.filter(
            game_id__in=game_ids, source="steam"
        ).values_list("game_id", "external_id"):
            try:
                steam_ids[game_id] = int(external_id)
            except (TypeError, ValueError):
                pass

        title_map = steam_title_map()
        appids = set()
        for game_id, title in Game.objects.filter(id__in=game_ids).values_list("id", "title"):
            appid = steam_ids.get(game_id) or title_map.get((title or "").strip().casefold())
            if appid is not None:
                appids.add(appid)
        return appids

    def list(self, request):
        model = load_taste_model()
        if model is None:
            return Response(
                {"detail": "K-Means clusters are missing or outdated. Run the kmeans job again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        user = request.user
        stored = read_profile(user)
        genres = list(
            UserGenre.objects.filter(user=user).select_related("genre")
            .values_list("genre__name", flat=True)
        )
        platforms = list(UserPlatform.objects.filter(user=user).values_list("platform", flat=True))
        liked_rows = [model.row_of[a] for a in stored["liked_game_ids"] if a in model.row_of]
        liked_matrix = model.matrix[liked_rows] if liked_rows else None

        preferences = {
            "genres": genres,
            "tags": stored["tags"],
            "platforms": platforms,
            "favorite_games_used": len(liked_rows),
        }
        interest = algorithms.interest_vector(
            model.index, model.idf, genres, stored["tags"], liked_matrix
        )
        if interest is None:
            return Response({
                "source": "kmeans_taste", "count": 0, "preferences": preferences, "results": [],
                "detail": "Pick at least one genre, tag or liked game to get recommendations.",
            })

        allowed = np.ones(len(model.appids), dtype=bool)
        known = [p for p in platforms if p in model.platform_rows]
        if known:  # the game must run on at least one chosen platform
            on_platform = np.zeros(len(model.appids), dtype=bool)
            for p in known:
                on_platform |= model.platform_rows[p]
            allowed &= on_platform
        for appid in set(stored["liked_game_ids"]) | self._library_appids(user):
            row = model.row_of.get(appid)
            if row is not None:
                allowed[row] = False

        limit = int_param(request, "limit", default=20, low=1, high=50)
        ranked = algorithms.rank_by_interest(
            model.matrix, model.labels, model.centroids, interest, allowed, model.quality, limit=limit
        )

        # "Because you like X": the liked game closest to each result
        games = steam_games()
        closest, closest_sim = None, None
        if ranked and liked_matrix is not None:
            sims = (model.matrix[[r for r, _, _ in ranked]] @ liked_matrix.T).toarray()
            closest, closest_sim = sims.argmax(axis=1), sims.max(axis=1)

        genre_tokens = {algorithms.term_token("genre", g) for g in genres}
        tag_tokens = {algorithms.term_token("tag", t) for t in stored["tags"]}

        results = []
        for position, (row, score, _) in enumerate(ranked):
            appid = model.appids[row]
            payload = steam_game_payload(appid)
            if payload is None:
                continue
            game = games[appid]
            similar_to = None
            if closest is not None and closest_sim[position] >= 0.3:
                similar_to = games[model.appids[liked_rows[int(closest[position])]]]["name"]
            cluster_id = int(model.labels[row])
            results.append({
                "game": payload,
                "score": round(score, 4),
                "rank": len(results) + 1,
                "cluster_id": cluster_id,
                "cluster_label": model.cluster_names.get(cluster_id),
                "reason": {
                    "matched_genres": [
                        g for g in game["genres"] if algorithms.term_token("genre", g) in genre_tokens
                    ],
                    "matched_tags": [
                        t for t in game["tags"][: algorithms.MAX_TAGS_PER_GAME]
                        if algorithms.term_token("tag", t) in tag_tokens
                    ],
                    "similar_to": similar_to,
                },
            })

        return Response({
            "source": "kmeans_taste",
            "count": len(results),
            "preferences": preferences,
            "results": results,
        })
