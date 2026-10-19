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

KEEP_RUNS = 2  # finished runs kept per algorithm; older ones (and their results) are deleted


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
    data = Interactions()
    data.ratings = {(u, g): s for u, g, s in Rating.objects.values_list("user_id", "game_id", "score")}
    data.likes = set(GameLike.objects.values_list("user_id", "game_id"))
    statuses = {(u, g): s for u, g, s in UserGameList.objects.values_list("user_id", "game_id", "status")}

    for key in set(data.ratings) | data.likes | set(statuses):
        value = algorithms.interaction_value(data.ratings.get(key), key in data.likes, statuses.get(key))
        if value is not None:
            data.values[key] = value
            data.seen.setdefault(key[0], set()).add(key[1])
    for user_id, game_id in UserDismissedGame.objects.values_list("user_id", "game_id"):
        data.seen.setdefault(user_id, set()).add(game_id)
    return data


def _game_documents(games):
    """(ids, documents, {id: (genre names, tag names)}) for games with prefetched genres/tags."""
    ids, docs, labels = [], [], {}
    for game in games:
        genres = [g.name for g in game.genres.all()]
        tags = [t.name for t in game.tags.all()]
        ids.append(game.id)
        docs.append(algorithms.build_game_document(genres, tags, game.description))
        labels[game.id] = (genres, tags)
    return ids, docs, labels


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
def build_similarities(top_n: int = 20, min_score: float = 0.05) -> ModelRun:
    def work(run):
        games = list(Game.objects.prefetch_related("genres", "tags").order_by("id"))
        if len(games) < 2:
            raise NotEnoughData("Need at least 2 games to compare.")
        ids, docs, labels = _game_documents(games)
        model = algorithms.build_tfidf(ids, docs)
        neighbours = algorithms.top_similar(model.matrix, k=top_n, min_score=min_score)

        rows = []
        for i, pairs in enumerate(neighbours):
            for rank, (j, score) in enumerate(pairs, start=1):
                genres_a, tags_a = labels[ids[i]]
                genres_b, tags_b = labels[ids[j]]
                rows.append(
                    GameSimilarity(
                        model_run=run,
                        game_id=ids[i],
                        similar_game_id=ids[j],
                        score=score,
                        rank=rank,
                        reason={
                            "genres": sorted(set(genres_a) & set(genres_b)),
                            "tags": sorted(set(tags_a) & set(tags_b))[:5],
                        },
                    )
                )
        GameSimilarity.objects.bulk_create(rows, batch_size=1000)
        return {"games": len(ids), "pairs": len(rows), "vocabulary": len(model.vectorizer.vocabulary_)}

    return _run_job("tfidf", "v1", {"top_n": top_n, "min_score": min_score}, work)


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
        return {
            "users": len(model.user_ids),
            "games": len(model.game_ids),
            "interactions": len(triples),
            "recommendations": len(rows),
            "factors": min(factors, min(len(model.user_ids), len(model.game_ids)) - 1),
            "train_rmse": round(model.train_rmse, 3),
        }

    params = {"factors": factors, "top_n": top_n, "min_users": min_users, "min_items": min_items}
    return _run_job("svd", "v1", params, work)


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


def build_clusters(k: int | None = None, dims: int = 50) -> ModelRun:
    def work(run):
        metrics, skipped = {}, []

        # games, grouped by what they are about
        try:
            games = list(Game.objects.prefetch_related("genres", "tags").order_by("id"))
            ids, docs, _ = _game_documents(games)
            model = algorithms.build_tfidf(ids, docs)
            result = algorithms.cluster_points(algorithms.reduce_for_clustering(model.matrix, dims), k)
            names = algorithms.label_clusters(model.matrix, result.labels, model.vectorizer.get_feature_names_out())
            metrics["game_clusters"] = _save_clusters(run, "game", result, names, ids, "game_id", None)
        except NotEnoughData as exc:
            skipped.append(f"games: {exc}")

        # players, grouped by the genres they gravitate to
        try:
            data = load_interactions()
            genres = {g.id: [x.name for x in g.genres.all()] for g in Game.objects.prefetch_related("genres")}
            user_ids, genre_names, matrix = algorithms.taste_matrix(data.values, genres)
            result = algorithms.cluster_points(matrix, k)
            names = algorithms.label_clusters(matrix, result.labels, genre_names, prefixes=None)
            metrics["user_clusters"] = _save_clusters(run, "user", result, names, user_ids, "user_id", None)
        except NotEnoughData as exc:
            skipped.append(f"players: {exc}")

        if not metrics:
            raise NotEnoughData("; ".join(skipped))
        if skipped:
            metrics["skipped"] = skipped
        return metrics

    return _run_job("kmeans", "v1", {"k": k, "dims": dims}, work)
