"""Database side of the algorithms: read the data, run an algorithm, save the results.

Every job follows the same pattern:
1. create a ModelRun row (status "running")
2. write all results tagged with that run, inside one transaction
3. only if everything worked, mark this run active and the previous one inactive
so a page never reads half-written results. If there is too little data the run is
recorded as "failed" with a readable reason and the old results stay live.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Avg, Count
from django.utils import timezone
import numpy as np
from apps.core.models import (
    Cluster,
    ClusterMember,
    Game,
    GameLike,
    GameScore,
    GameSimilarity,
    ModelRun,
    Rating,
    Review,
    UserDismissedGame,
    UserGameList,
    UserRecommendation,
)

from . import algorithms
from .algorithms import NotEnoughData

import json
from pathlib import Path

KEEP_RUNS = 2  # finished runs kept per algorithm; older ones (and their results) are deleted

import json
import os
import tempfile

from pathlib import Path
from dataclasses import dataclass


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STEAM_JSON_PATH = PROJECT_ROOT / "data" / "steam_games_reduced.json"
RECOMMENDATION_DIR = PROJECT_ROOT / "data" / "recommendations"

TOP_N = 20


@dataclass
class FileJobRun:
    status: str
    metrics: dict
    error: str | None = None


def _as_names(value):
    """Convert JSON strings, lists, or dictionaries into a list of names."""
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            if isinstance(item, dict):
                name = item.get("description") or item.get("name") or key
            else:
                name = key
            if name:
                result.append(str(name))
        return result

    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("description") or item.get("name")
            else:
                name = item
            if name:
                result.append(str(name))
        return result

    if isinstance(value, str) and value.strip():
        return [value.strip()]

    return []

def _to_int(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _vote_count(metadata) -> int:
    """Review count if the JSON has one (positive+negative, or recommendations)."""
    votes = _to_int(metadata.get("positive")) + _to_int(metadata.get("negative"))
    if votes:
        return votes
    recommendations = metadata.get("recommendations")
    if isinstance(recommendations, dict):
        recommendations = recommendations.get("total")
    return _to_int(recommendations)


def _platforms(metadata) -> list[str]:
    """Works with {"platforms": {"windows": true}}, ["windows"], "windows,mac" or top-level windows: true."""
    raw = metadata.get("platforms")
    if isinstance(raw, dict):
        enabled = {str(k).lower() for k, v in raw.items() if v is True}
    elif isinstance(raw, list):
        enabled = {str(x).lower() for x in raw}
    elif isinstance(raw, str):
        enabled = {x.strip().lower() for x in raw.split(",")}
    else:
        enabled = set()
    if "macos" in enabled:
        enabled.add("mac")
    return [p for p in ("windows", "mac", "linux") if p in enabled or metadata.get(p) is True]


def _load_steam_games():
    """Load game metadata directly from the reduced Steam JSON file."""
    if not STEAM_JSON_PATH.exists():
        raise FileNotFoundError(
            f"Steam JSON dataset not found: {STEAM_JSON_PATH}"
        )

    with STEAM_JSON_PATH.open("r", encoding="utf-8") as file:
        raw_data = json.load(file)

    if isinstance(raw_data, dict):
        entries = raw_data.items()
    elif isinstance(raw_data, list):
        entries = (
            (item.get("appid") or item.get("id"), item)
            for item in raw_data
            if isinstance(item, dict)
        )
    else:
        raise ValueError("Steam JSON must contain a dictionary or a list.")

    games = {}

    for raw_id, metadata in entries:
        if not isinstance(metadata, dict):
            continue

        try:
            appid = int(raw_id)
        except (TypeError, ValueError):
            continue

        name = metadata.get("name")
        if not name:
            continue

        genres = _as_names(metadata.get("genres"))
        categories = _as_names(metadata.get("categories"))
        tags = _as_names(metadata.get("tags"))

        # Remove duplicate names while preserving their order.
        genres = list(dict.fromkeys(genres))
        categories = list(dict.fromkeys(categories))
        tags = list(dict.fromkeys(tags))

        description = (
            metadata.get("short_description")
            or metadata.get("description")
            or ""
        )

        games[appid] = {
            "appid": appid,
            "name": str(name),
            "header_image": metadata.get("header_image"),
            "release_date": metadata.get("release_date"),
            "genres": genres,
            "categories": categories,
            "tags": tags,
            "description": str(description),
            "platforms": _platforms(metadata),
            "votes": _vote_count(metadata),
        }

        platforms = [
            platform
            for platform in ("windows", "mac", "linux")
            if metadata.get(platform) is True
        ]

    if not games:
        raise ValueError("No valid games were found in the Steam JSON file.")

    return games


def _build_documents(games):
    """Build TF-IDF documents using JSON metadata only."""
    appids = []
    documents = []

    for appid, game in games.items():
        document = algorithms.build_game_document(
            genres=game["genres"] + game["categories"],
            tags=game["tags"],
            description=game["description"],
        )

        if not document.strip():
            continue

        appids.append(appid)
        documents.append(document)

    if not documents:
        raise ValueError("No usable game documents were found.")

    return appids, documents


def _write_json_atomic(path, data):
    """Write a JSON artifact atomically to avoid leaving partial output."""
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".tmp",
            delete=False,
        ) as file:
            temp_path = file.name
            json.dump(
                data,
                file,
                ensure_ascii=False,
                separators=(",", ":"),
            )

        os.replace(temp_path, path)

    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def _file_job(work):
    """Run a file-only job without creating database records."""
    try:
        metrics = work()
        return FileJobRun(status="succeeded", metrics=metrics)
    except Exception as exc:
        return FileJobRun(
            status="failed",
            metrics={},
            error=f"{type(exc).__name__}: {exc}",
        )


# ─── Run bookkeeping ──────────────────────────────────────────────────────────
def _fail(run: ModelRun, message: str) -> None:
    run.status = ModelRun.Status.FAILED
    run.error = message[:2000]
    run.is_active = False
    run.finished_at = timezone.now()
    run.save()


def _prune(algorithm: str, keep: int = KEEP_RUNS) -> None:
    old = list(
        ModelRun.objects.filter(algorithm=algorithm).order_by("-id").values_list("id", flat=True)[keep:]
    )
    ModelRun.objects.filter(id__in=old).exclude(is_active=True).delete()


def _run_job(algorithm: str, version: str, params: dict, work) -> ModelRun:
    run = ModelRun.objects.create(algorithm=algorithm, version=version, params=params)
    try:
        with transaction.atomic():
            metrics = work(run)
            ModelRun.objects.filter(algorithm=algorithm, is_active=True).update(is_active=False)
            run.status = ModelRun.Status.SUCCEEDED
            run.is_active = True
            run.metrics = metrics
            run.finished_at = timezone.now()
            run.save()
    except NotEnoughData as exc:
        _fail(run, str(exc))
        return run
    except Exception as exc:
        _fail(run, f"{type(exc).__name__}: {exc}")
        raise
    _prune(algorithm)
    return run


# ─── Shared reads ─────────────────────────────────────────────────────────────
@dataclass
class Interactions:
    values: dict[tuple[int, int], float] = field(default_factory=dict)  # (user, game) -> 1-10
    ratings: dict[tuple[int, int], int] = field(default_factory=dict)
    likes: set[tuple[int, int]] = field(default_factory=set)
    seen: dict[int, set[int]] = field(default_factory=dict)  # games never to recommend again


def load_interactions() -> Interactions:
    """Ratings, likes and list statuses of active users, one value per (user, game).
    A rating wins over a like, a like over a status (see algorithms.interaction_value)."""
    data = Interactions()
    data.ratings = {
        (u, g): s
        for u, g, s in Rating.objects.filter(
            user__is_active=True, score__gte=1, score__lte=10
        ).values_list("user_id", "game_id", "score")
    }
    data.likes = set(
        GameLike.objects.filter(user__is_active=True).values_list("user_id", "game_id")
    )
    statuses = {
        (u, g): s
        for u, g, s in UserGameList.objects.filter(user__is_active=True).values_list(
            "user_id", "game_id", "status"
        )
    }

    for key in set(data.ratings) | data.likes | set(statuses):
        value = algorithms.interaction_value(data.ratings.get(key), key in data.likes, statuses.get(key))
        if value is not None:
            data.values[key] = value
            data.seen.setdefault(key[0], set()).add(key[1])
    for user_id, game_id in UserDismissedGame.objects.values_list("user_id", "game_id"):
        data.seen.setdefault(user_id, set()).add(game_id)
    return data



def _as_names(value):
    """Normalize JSON genres, categories, and tags into a list of names."""
    if isinstance(value, dict):
        return [str(name) for name in value.keys() if name]
    if isinstance(value, list):
        names = []
        for item in value:
            if isinstance(item, dict):
                name = item.get("description") or item.get("name")
                if name:
                    names.append(str(name))
            elif item:
                names.append(str(item))
        return names
    if isinstance(value, str) and value:
        return [value]
    return []


def _load_json_game_documents():
    """Load metadata from the reduced Steam JSON and match it to database games."""
    json_path = Path(__file__).resolve().parents[2] / "data" / "steam_games_reduced.json"

    if not json_path.exists():
        raise FileNotFoundError(
            f"Steam metadata JSON not found: {json_path}"
        )

    with json_path.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    if isinstance(metadata, list):
        entries = [
            (str(item.get("appid") or item.get("id") or ""), item)
            for item in metadata
            if isinstance(item, dict)
        ]
    elif isinstance(metadata, dict):
        entries = list(metadata.items())
    else:
        raise ValueError("Steam metadata JSON must contain a list or object.")

    database_games = list(Game.objects.only("id", "title"))
    title_map = {}

    for game in database_games:
        title = (game.title or "").strip().casefold()
        if title:
            title_map.setdefault(title, game)

    ids = []
    documents = []
    labels = {}
    matched_ids = set()

    for app_id, item in entries:
        if not isinstance(item, dict):
            continue

        title = str(item.get("name") or "").strip()
        game = title_map.get(title.casefold())

        # Results are saved to tables with foreign keys to Game.
        if game is None or game.id in matched_ids:
            continue

        genres = _as_names(item.get("genres"))
        tags = _as_names(item.get("tags"))
        categories = _as_names(item.get("categories"))

        # Include categories as additional descriptive metadata.
        tags = list(dict.fromkeys(tags + categories))

        description = (
            item.get("short_description")
            or item.get("description")
            or ""
        )

        ids.append(game.id)
        documents.append(
            algorithms.build_game_document(genres, tags, description)
        )
        labels[game.id] = (genres, tags)
        matched_ids.add(game.id)

    if not ids:
        raise NotEnoughData(
            "No Steam JSON games matched database Game titles. "
            "Check the game titles or use Steam app IDs to map records."
        )

    return ids, documents, labels, len(entries)



def _counts(queryset) -> dict[int, int]:
    return {row["game_id"]: row["n"] for row in queryset.values("game_id").annotate(n=Count("id"))}


# ─── 1. Bayesian scores -> game_scores ────────────────────────────────────────
def build_bayesian_scores(min_votes: float | None = None) -> ModelRun:
    def work(run):
        stats = {
            row["game_id"]: (float(row["avg"]), row["n"])
            for row in Rating.objects.values("game_id").annotate(avg=Avg("score"), n=Count("id"))
        }
        result = algorithms.bayesian_weighted_rating(stats, min_votes)
        likes = _counts(GameLike.objects.all())
        listed = _counts(UserGameList.objects.all())
        reviews = _counts(Review.objects.filter(deleted_at__isnull=True, status=Review.Status.PUBLISHED))

        rows = []
        for game_id in Game.objects.values_list("id", flat=True):
            average, n = stats.get(game_id, (None, 0))
            weighted = result.scores.get(game_id)
            rows.append(
                GameScore(
                    game_id=game_id,
                    average_rating=round(average, 2) if average is not None else None,
                    rating_count=n,
                    bayesian_score=round(weighted, 3) if weighted is not None else None,
                    popularity_score=algorithms.popularity(
                        n, likes.get(game_id, 0), listed.get(game_id, 0), reviews.get(game_id, 0)
                    ),
                )
            )
        # trending_score is left alone: the trending job owns it
        GameScore.objects.bulk_create(
            rows,
            batch_size=1000,
            update_conflicts=True,
            unique_fields=["game"],
            update_fields=["average_rating", "rating_count", "bayesian_score", "popularity_score", "updated_at"],
        )
        return {
            "games": len(rows),
            "rated_games": len(stats),
            "global_mean": round(result.global_mean, 3) if result.global_mean is not None else None,
            "min_votes": round(result.min_votes, 2),
        }

    return _run_job("bayesian", "v1", {"min_votes": min_votes}, work)


# ─── 2. TF-IDF + cosine -> game_similarities ──────────────────────────────────
def build_similarities(top_n=20, **kwargs):
    def work():
        games = _load_steam_games()
        appids, documents = _build_documents(games)

        model = algorithms.build_tfidf(appids, documents)
        matrix = model.matrix

        results = {}
        chunk_size = 16
        total = len(appids)

        for start in range(0, total, chunk_size):
            end = min(start + chunk_size, total)

            # Only this small chunk is converted into a dense matrix.
            similarities = (
                matrix[start:end] @ matrix.T
            ).toarray()

            for local_index, scores in enumerate(similarities):
                game_index = start + local_index
                source_appid = appids[game_index]

                # Never recommend the game itself.
                scores[game_index] = 0.0

                # Find the highest-scoring candidates.
                candidate_count = min(top_n, total - 1)
                if candidate_count <= 0:
                    results[str(source_appid)] = []
                    continue

                candidate_indices = np.argpartition(
                    scores, -candidate_count
                )[-candidate_count:]

                candidate_indices = sorted(
                    candidate_indices,
                    key=lambda index: scores[index],
                    reverse=True,
                )

                source_game = games[source_appid]
                source_terms = set(
                    source_game["genres"]
                    + source_game["categories"]
                    + source_game["tags"]
                )

                similar_games = []

                for candidate_index in candidate_indices:
                    score = float(scores[candidate_index])

                    if score <= 0:
                        continue

                    similar_appid = appids[candidate_index]
                    similar_game = games[similar_appid]

                    similar_terms = set(
                        similar_game["genres"]
                        + similar_game["categories"]
                        + similar_game["tags"]
                    )

                    shared_terms = sorted(source_terms & similar_terms)

                    similar_games.append({
                        "appid": similar_appid,
                        "score": round(score, 6),
                        "rank": len(similar_games) + 1,
                        "reason": {
                            "shared_features": shared_terms[:10],
                        },
                    })

                results[str(source_appid)] = similar_games

        artifact = {
            "source": "steam_games_reduced.json",
            "game_count": len(appids),
            "top_n": top_n,
            "games": results,
        }

        output_path = RECOMMENDATION_DIR / "tfidf_similarities.json"
        _write_json_atomic(output_path, artifact)

        return {
            "json_games": len(games),
            "games_processed": len(appids),
            "games_with_results": len(results),
            "top_n": top_n,
            "output": str(output_path),
        }

    return _file_job(work)


# ─── 3. SVD -> user_recommendations ───────────────────────────────────────────
def _explain(user_id, reason_game_id, data: Interactions, titles: dict[int, str]):
    ReasonType = UserRecommendation.ReasonType
    if reason_game_id is None:
        return ReasonType.COLLABORATIVE, "Players with similar taste liked this"
    title = titles.get(reason_game_id, "a game you played")
    key = (user_id, reason_game_id)
    if key in data.likes or (data.ratings.get(key) or 0) >= 8:
        return ReasonType.BECAUSE_LIKED, f"Because you liked {title}"[:255]
    return ReasonType.BECAUSE_PLAYED, f"Because you played {title}"[:255]


def build_recommendations(
    factors: int = 20, top_n: int = 20, min_users: int = 5, min_items: int = 5
) -> ModelRun:
    """"Players Like You": collaborative filtering over every active player's interactions."""
    def work(run):
        data = load_interactions()
        triples = [(u, g, v) for (u, g), v in data.values.items()]
        model = algorithms.fit_svd(triples, factors=factors, min_users=min_users, min_items=min_items)

        positives: dict[int, set[int]] = {}
        for (user_id, game_id), value in data.values.items():
            if value >= algorithms.POSITIVE_VALUE:
                positives.setdefault(user_id, set()).add(game_id)
        recs = algorithms.recommend_svd(model, data.seen, positives, top_n=top_n)

        titles = dict(Game.objects.values_list("id", "title"))
        rows = []
        for user_id, items in recs.items():
            for rank, rec in enumerate(items, start=1):
                reason_type, reason_text = _explain(user_id, rec.reason_game_id, data, titles)
                rows.append(
                    UserRecommendation(
                        model_run=run,
                        user_id=user_id,
                        game_id=rec.game_id,
                        score=round(rec.score, 3),
                        rank=rank,
                        reason_type=reason_type,
                        reason_game_id=rec.reason_game_id,
                        reason_text=reason_text,
                    )
                )
        UserRecommendation.objects.bulk_create(rows, batch_size=1000)
        all_users = {u for u, _ in data.values}
        return {
            "users": len(model.user_ids),
            "users_without_enough_activity": len(all_users) - len(model.user_ids),
            "users_with_recommendations": len(recs),
            "games": len(model.game_ids),
            "interactions": len(triples),
            "recommendations": len(rows),
            "factors": model.factors,
            "train_rmse": round(model.train_rmse, 3),
        }

    params = {"factors": factors, "top_n": top_n, "min_users": min_users, "min_items": min_items}
    return _run_job("svd", "v2", params, work)


# ─── 4. K-Means -> clusters + cluster_members ─────────────────────────────────
def _save_clusters(run, entity_type, result, names, member_ids, member_field, extra) -> int:
    for cluster_index in range(result.k):
        members = [i for i, label in enumerate(result.labels) if int(label) == cluster_index]
        if not members:
            continue
        terms = names.get(cluster_index, [])
        cluster = Cluster.objects.create(
            model_run=run,
            entity_type=entity_type,
            label=" / ".join(terms) or f"Cluster {cluster_index + 1}",
            centroid={"vector": [round(float(x), 4) for x in result.centroids[cluster_index]], "top_terms": terms},
            size=len(members),
        )
        ClusterMember.objects.bulk_create(
            [
                ClusterMember(
                    cluster=cluster,
                    **{member_field: member_ids[i]},
                    distance=float(result.distances[i]),
                    pca_x=float(result.coords[i][0]),
                    pca_y=float(result.coords[i][1]),
                )
                for i in members
            ],
            batch_size=1000,
        )
    return result.k


def build_clusters(k=None, **kwargs):
    def work():
        games = _load_steam_games()

        appids, documents = [], []
        for appid, game in games.items():
            document = algorithms.kmeans_document(game["genres"], game["tags"])
            if document:
                appids.append(appid)
                documents.append(document)
        if not documents:
            raise NotEnoughData("No games with genres or tags were found in the Steam JSON.")

        model = algorithms.build_tfidf(appids, documents)
        matrix = model.matrix
        vocabulary = [str(term) for term in model.vectorizer.get_feature_names_out()]
        idf = model.vectorizer.idf_

        reduced = algorithms.reduce_for_clustering(matrix, dims=50)
        clustering = algorithms.cluster_points(reduced, k=k, min_points=6, seed=42)
        centroids = algorithms.term_centroids(matrix, clustering.labels, clustering.k)

        # token -> the name Steam uses (for readable cluster labels)
        display = {}
        for game in games.values():
            for genre in game["genres"]:
                display.setdefault(algorithms.term_token("genre", genre), genre)
            for tag in game["tags"][: algorithms.MAX_TAGS_PER_GAME]:
                display.setdefault(algorithms.term_token("tag", tag), tag)

        clusters = []
        for cluster_id in range(clustering.k):
            member_rows = np.flatnonzero(clustering.labels == cluster_id)
            if member_rows.size == 0:
                continue
            weights = centroids[cluster_id]
            top = [i for i in np.argsort(-weights)[:3] if weights[i] > 0]
            top_terms = [display.get(vocabulary[i], vocabulary[i]) for i in top]
            clusters.append({
                "id": cluster_id,
                "label": " / ".join(top_terms) or f"Cluster {cluster_id + 1}",
                "size": int(member_rows.size),
                "top_terms": top_terms,
                "centroid": {
                    vocabulary[i]: round(float(weights[i]), 4)
                    for i in np.flatnonzero(weights >= 0.02)
                },
                "games": [
                    {
                        "appid": appids[r],
                        "distance": round(float(clustering.distances[r]), 6),
                        "pca_x": round(float(clustering.coords[r][0]), 6),
                        "pca_y": round(float(clustering.coords[r][1]), 6),
                    }
                    for r in member_rows
                ],
            })

        artifact = {
            "version": 2,
            "source": "steam_games_reduced.json",
            "game_count": len(appids),
            "cluster_count": len(clusters),
            "vocabulary": vocabulary,
            "idf": [round(float(x), 6) for x in idf],
            "clusters": clusters,
        }
        output_path = RECOMMENDATION_DIR / "game_clusters.json"
        _write_json_atomic(output_path, artifact)

        return {
            "json_games": len(games),
            "games_clustered": len(appids),
            "game_clusters": len(clusters),
            "terms": len(vocabulary),
            "output": str(output_path),
        }

    return _file_job(work)