"""Recommendation algorithms. Pure functions: numbers in, numbers out, no database access.

- Bayesian weighted rating      fair community score
- TF-IDF + cosine similarity    "Similar titles"
- SVD matrix factorization      "Recommended for you"
- K-Means (+ PCA coordinates)   groups of similar games and similar players
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import svds
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA, TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score


class NotEnoughData(Exception):
    """There is too little data for the algorithm to say anything meaningful."""


# ─── How much each kind of activity says about taste ──────────────────────────
# A rating always wins. Without one, a like beats list status, and list status beats nothing,
# which keeps "ratings and likes weigh more than just owning a game".
LIKE_VALUE = 8.0
STATUS_VALUE = {
    "completed": 7.0,
    "playing": 6.5,
    "plan_to_play": 5.5,
    "on_hold": 5.0,
    "dropped": 3.0,
}
POSITIVE_VALUE = 7.0  # at or above this counts as "the user liked it"
POPULARITY_WEIGHTS = {"ratings": 1.0, "likes": 2.0, "list_entries": 1.0, "reviews": 2.0}


def interaction_value(rating, liked: bool, status) -> float | None:
    """One 1-10 number for a user and a game, or None if there is no signal at all."""
    if rating is not None:
        return float(rating)
    if liked:
        return LIKE_VALUE
    if status:
        return STATUS_VALUE.get(status)
    return None


def popularity(ratings: int, likes: int, list_entries: int, reviews: int) -> float:
    w = POPULARITY_WEIGHTS
    return (
        ratings * w["ratings"] + likes * w["likes"]
        + list_entries * w["list_entries"] + reviews * w["reviews"]
    )


# ─── 1. Bayesian weighted rating ──────────────────────────────────────────────
@dataclass
class BayesianResult:
    global_mean: float | None  # C: the average of every rating on the site
    min_votes: float  # m: how many ratings a game needs before its own average counts fully
    scores: dict[int, float]  # game_id -> weighted rating


def bayesian_weighted_rating(
    stats: dict[int, tuple[float, int]], min_votes: float | None = None
) -> BayesianResult:
    """WR = v/(v+m) * R + m/(v+m) * C, where R is the game's average and v its rating count.

    stats maps game_id -> (average rating, number of ratings) for games with at least one rating.
    Few ratings pull the score toward the site-wide average, so one 10/10 can't beat a game
    that thousands of players rated 9.
    """
    if not stats:
        return BayesianResult(None, float(min_votes or 0), {})
    averages = np.array([a for a, _ in stats.values()], dtype=float)
    counts = np.array([n for _, n in stats.values()], dtype=float)
    global_mean = float((averages * counts).sum() / counts.sum())
    m = float(min_votes) if min_votes is not None else max(3.0, float(np.median(counts)))
    scores = {
        game_id: float((n / (n + m)) * avg + (m / (n + m)) * global_mean)
        for game_id, (avg, n) in stats.items()
    }
    return BayesianResult(global_mean, m, scores)


# ─── 2. TF-IDF + cosine similarity ────────────────────────────────────────────
def _token(prefix: str, text: str) -> str:
    return f"{prefix}_{re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')}"


def build_game_document(genres, tags, description, genre_weight: int = 3, tag_weight: int = 2) -> str:
    """Text that describes a game. Genres and tags are repeated so they count for more than
    the words of the description, and each one stays a single token (genre_role_playing)."""
    tokens = [_token("genre", g) for g in genres] * genre_weight
    tokens += [_token("tag", t) for t in tags] * tag_weight
    return " ".join(tokens + [description or ""])


@dataclass
class TfidfModel:
    game_ids: list[int]
    matrix: sparse.csr_matrix  # one L2-normalised row per game
    vectorizer: TfidfVectorizer


def build_tfidf(game_ids, documents) -> TfidfModel:
    if len(game_ids) != len(documents):
        raise ValueError("game_ids and documents must have the same length")
    vectorizer = TfidfVectorizer(stop_words="english", sublinear_tf=True)
    try:
        matrix = vectorizer.fit_transform(documents)
    except ValueError as exc:  # empty vocabulary
        raise NotEnoughData("No usable genres, tags or descriptions to compare games.") from exc
    return TfidfModel(list(game_ids), matrix.tocsr(), vectorizer)


def top_similar(matrix: sparse.csr_matrix, k: int = 20, min_score: float = 0.0, chunk: int = 500):
    """For every row, its k most similar other rows as [(row_index, cosine_score), ...].

    Rows are L2-normalised, so a plain dot product is the cosine similarity. Works in chunks
    so a big catalogue never needs one huge matrix in memory.
    """
    n = matrix.shape[0]
    k = min(k, n - 1)
    if k <= 0:
        return [[] for _ in range(n)]
    result: list[list[tuple[int, float]]] = []
    for start in range(0, n, chunk):
        sims = (matrix[start:start + chunk] @ matrix.T).toarray()
        for r in range(sims.shape[0]):
            sims[r, start + r] = -np.inf  # never "similar to itself"
        top = np.argpartition(-sims, k - 1, axis=1)[:, :k]
        for r in range(sims.shape[0]):
            ordered = top[r][np.argsort(-sims[r, top[r]])]
            result.append(
                [(int(j), min(float(sims[r, j]), 1.0)) for j in ordered if sims[r, j] > min_score]
            )
    return result


# ─── 3. SVD matrix factorization ──────────────────────────────────────────────
@dataclass
class SvdModel:
    user_ids: list[int]
    game_ids: list[int]
    user_means: np.ndarray  # each user's average value (their personal "baseline")
    user_factors: np.ndarray  # U * sigma, shape (users, k)
    item_factors: np.ndarray  # V^T, shape (k, games)
    train_rmse: float


@dataclass
class Recommendation:
    game_id: int
    score: float  # predicted 1-10 value for this user
    reason_game_id: int | None  # the game the user liked that this one is closest to


def fit_svd(interactions, factors: int = 20, min_users: int = 5, min_items: int = 5) -> SvdModel:
    """interactions: [(user_id, game_id, value 1-10), ...].

    Each user's values are centered on their own average (a harsh rater's 6 and a generous
    rater's 9 mean the same), then the user x game matrix is factorised with a truncated SVD.
    The hidden factors are taste patterns; multiplying them back fills in the empty cells.
    """
    if not interactions:
        raise NotEnoughData("No ratings, likes or list entries to learn from yet.")
    user_ids = sorted({u for u, _, _ in interactions})
    game_ids = sorted({g for _, g, _ in interactions})
    if len(user_ids) < min_users or len(game_ids) < min_items:
        raise NotEnoughData(
            f"Need at least {min_users} users and {min_items} games with activity; "
            f"found {len(user_ids)} users and {len(game_ids)} games."
        )
    u_index = {u: i for i, u in enumerate(user_ids)}
    g_index = {g: i for i, g in enumerate(game_ids)}
    rows = np.array([u_index[u] for u, _, _ in interactions])
    cols = np.array([g_index[g] for _, g, _ in interactions])
    vals = np.array([v for _, _, v in interactions], dtype=float)

    means = np.bincount(rows, weights=vals, minlength=len(user_ids)) / np.bincount(
        rows, minlength=len(user_ids)
    )
    centered = vals - means[rows]
    matrix = sparse.csr_matrix((centered, (rows, cols)), shape=(len(user_ids), len(game_ids)))

    k = min(factors, min(matrix.shape) - 1)
    if k < 1 or not np.any(centered):
        raise NotEnoughData("Ratings are too uniform to learn anyone's taste from.")
    u, s, vt = svds(matrix, k=k)

    user_factors = u * s
    predicted = np.einsum("ij,ji->i", user_factors[rows], vt[:, cols]) + means[rows]
    rmse = float(np.sqrt(np.mean((np.clip(predicted, 1, 10) - vals) ** 2)))
    return SvdModel(user_ids, game_ids, means, user_factors, vt, rmse)


def recommend_svd(
    model: SvdModel,
    seen: dict[int, set[int]],
    positives: dict[int, set[int]],
    top_n: int = 20,
    chunk: int = 500,
) -> dict[int, list[Recommendation]]:
    """Top predicted games per user, skipping everything in `seen`.

    reason_game_id is honest: it is the game among the user's liked ones that sits closest
    to the recommendation in the learned taste space, which is what drives the prediction.
    """
    g_index = {g: i for i, g in enumerate(model.game_ids)}
    items = model.item_factors.T  # (games, k)
    norms = np.linalg.norm(items, axis=1, keepdims=True)
    norms[norms == 0] = 1
    unit = items / norms

    results: dict[int, list[Recommendation]] = {}
    for start in range(0, len(model.user_ids), chunk):
        end = min(start + chunk, len(model.user_ids))
        scores = model.user_factors[start:end] @ model.item_factors + model.user_means[start:end, None]
        for r in range(end - start):
            user_id = model.user_ids[start + r]
            row = scores[r].copy()
            for game_id in seen.get(user_id, ()):
                if game_id in g_index:
                    row[g_index[game_id]] = -np.inf
            available = int(np.isfinite(row).sum())
            if available == 0:
                continue
            n = min(top_n, available)
            top = np.argpartition(-row, n - 1)[:n]
            top = top[np.argsort(-row[top])]  # rank on the raw score, clip only for display
            liked = [g_index[g] for g in positives.get(user_id, ()) if g in g_index]
            recs = []
            for idx in top:
                reason = None
                if liked:
                    sims = unit[liked] @ unit[idx]
                    best = int(np.argmax(sims))
                    if sims[best] > 0:
                        reason = model.game_ids[liked[best]]
                recs.append(
                    Recommendation(model.game_ids[idx], float(np.clip(row[idx], 1.0, 10.0)), reason)
                )
            results[user_id] = recs
    return results


# ─── 4. K-Means (+ PCA for plotting) ──────────────────────────────────────────
@dataclass
class ClusterResult:
    k: int
    labels: np.ndarray
    distances: np.ndarray  # distance of every point to its own cluster centre
    centroids: np.ndarray
    coords: np.ndarray  # 2-D PCA coordinates, for drawing the clusters


def reduce_for_clustering(matrix, dims: int = 50) -> np.ndarray:
    """Squeezes sparse TF-IDF vectors into a few dense dimensions (LSA), then normalises them,
    which makes K-Means behave like clustering by cosine similarity."""
    n, features = matrix.shape
    d = min(dims, n - 1, features - 1)
    if d >= 2:
        reduced = TruncatedSVD(n_components=d, random_state=42).fit_transform(matrix)
    else:
        reduced = matrix.toarray() if sparse.issparse(matrix) else np.asarray(matrix)
    norms = np.linalg.norm(reduced, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return reduced / norms


def _distinct(points: np.ndarray) -> int:
    return len(np.unique(points.round(8), axis=0))


def choose_k(points, k_min=2, k_max=10, seed=42):
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    count = points.shape[0]

    if count < 3:
        raise NotEnoughData(
            f"Need at least 3 items to choose clusters; found {count}."
        )

    # Evaluate possible cluster counts on a sample, not the full dataset.
    sample_size = min(5000, count)
    rng = np.random.default_rng(seed)

    if count > sample_size:
        indices = rng.choice(count, size=sample_size, replace=False)
        sample = points[indices]
    else:
        sample = points

    distinct_count = np.unique(sample, axis=0).shape[0]
    upper_k = min(k_max, distinct_count - 1)

    if upper_k < k_min:
        return min(2, distinct_count)

    best_k = k_min
    best_score = -1.0

    for k in range(k_min, upper_k + 1):
        model = KMeans(
            n_clusters=k,
            random_state=seed,
            n_init=10,
        )
        labels = model.fit_predict(sample)

        if len(np.unique(labels)) < 2:
            continue

        score = silhouette_score(sample, labels)

        if score > best_score:
            best_score = score
            best_k = k

    return best_k


def pca_coordinates(points: np.ndarray, seed: int = 42) -> np.ndarray:
    if points.shape[1] < 2:
        coords = np.zeros((points.shape[0], 2))
        coords[:, : points.shape[1]] = points
        return coords
    return PCA(n_components=2, random_state=seed).fit_transform(points)


def cluster_points(points: np.ndarray, k: int | None = None, min_points: int = 6, seed: int = 42) -> ClusterResult:
    n = points.shape[0]
    if n < min_points:
        raise NotEnoughData(f"Need at least {min_points} items to cluster; found {n}.")
    distinct = _distinct(points)
    if distinct < 2:
        raise NotEnoughData("All items look identical, so there is nothing to separate into groups.")
    k = max(2, min(k or choose_k(points, seed=seed), n - 1, distinct))
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(points)
    distances = np.linalg.norm(points - km.cluster_centers_[km.labels_], axis=1)
    return ClusterResult(k, km.labels_, distances, km.cluster_centers_, pca_coordinates(points, seed))


def _pretty(name: str) -> str:
    return re.sub(r"^(genre|tag)_", "", name).replace("_", " ").title()


def label_clusters(matrix, labels, feature_names, prefixes=("genre_", "tag_"), top_terms: int = 2) -> dict[int, list[str]]:
    """Names each cluster after its strongest genres/tags (matrix may be sparse or dense)."""
    names = np.asarray(feature_names)
    eligible = (
        np.array([n.startswith(prefixes) for n in names]) if prefixes else np.ones(len(names), dtype=bool)
    )
    if not eligible.any():
        eligible[:] = True
    out: dict[int, list[str]] = {}
    for cluster in sorted(set(int(c) for c in labels)):
        mean = np.asarray(matrix[labels == cluster].mean(axis=0)).ravel()
        strength = np.where(eligible, mean, -1.0)
        best = np.argsort(-strength)[:top_terms]
        out[cluster] = [_pretty(str(names[i])) for i in best if mean[i] > 0]
    return out


def taste_matrix(values: dict[tuple[int, int], float], game_genres: dict[int, list[str]], min_interactions: int = 3):
    """Players x genres affinity (each row L2-normalised), for clustering players by taste.

    Returns (user_ids, genre_names, matrix). Players with fewer than min_interactions are left out.
    """
    per_user: dict[int, list[tuple[int, float]]] = {}
    for (user_id, game_id), value in values.items():
        per_user.setdefault(user_id, []).append((game_id, value))
    genre_names = sorted({g for genres in game_genres.values() for g in genres})
    if not genre_names:
        raise NotEnoughData("Games have no genres, so player tastes can't be compared.")
    g_index = {g: i for i, g in enumerate(genre_names)}
    user_ids, rows = [], []
    for user_id, items in sorted(per_user.items()):
        if len(items) < min_interactions:
            continue
        row = np.zeros(len(genre_names))
        for game_id, value in items:
            for genre in game_genres.get(game_id, ()):
                row[g_index[genre]] += value / 10.0
        norm = np.linalg.norm(row)
        if norm > 0:
            user_ids.append(user_id)
            rows.append(row / norm)
    if not rows:
        raise NotEnoughData("No players have enough activity to compare yet.")
    return user_ids, genre_names, np.vstack(rows)
