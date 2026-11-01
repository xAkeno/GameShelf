import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import requests
from django.utils import timezone

from apps.core.models.games import SteamAppCache
from apps.games.serializers import get_steam_game_details, get_steam_game_reviews

logger = logging.getLogger(__name__)

DETAILS_TTL = timedelta(days=7)     # prices and descriptions change slowly
NOT_FOUND_TTL = timedelta(days=1)   # remember "not a game" so we stop asking Steam
RATING_TTL = timedelta(hours=12)
MAX_WORKERS = 8

# Big fields the frontend never reads: don't store them.
DROP_FIELDS = ("detailed_description", "package_groups")

_FAILED = object()  # "Steam didn't answer" (different from "Steam says it doesn't exist")


def _slim(data):
    if not data:
        return data
    return {k: v for k, v in data.items() if k not in DROP_FIELDS}


def _is_fresh(row):
    ttl = NOT_FOUND_TTL if row.not_found else DETAILS_TTL
    return timezone.now() - row.fetched_at < ttl


def _fetch_details(app_id):
    try:
        return get_steam_game_details(app_id)  # dict, or None if Steam says not found
    except (requests.RequestException, ValueError):
        logger.warning("Steam details failed for %s", app_id)
        return _FAILED


def _save_details(app_id, data):
    # rating_summary is not in `defaults`, so a refresh keeps it
    SteamAppCache.objects.update_or_create(
        app_id=app_id,
        defaults={
            "data": data,
            "not_found": data is None,
            "fetched_at": timezone.now(),
        },
    )


# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------
def load_details(app_ids):
    """
    Returns ({app_id: details dict or None}, failed_ids).

    1 query for everything we already have; Steam is only called (in parallel)
    for games that are missing or stale. If Steam is down, stale data is served.
    DB access stays in the main thread; worker threads only do HTTP.
    """
    ids = list(dict.fromkeys(int(a) for a in app_ids))
    rows = SteamAppCache.objects.in_bulk(ids)

    result, failed, to_fetch = {}, set(), []
    for app_id in ids:
        row = rows.get(app_id)
        if row and _is_fresh(row):
            result[app_id] = None if row.not_found else row.data
        else:
            to_fetch.append(app_id)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_fetch))) as pool:
            fetched = list(pool.map(_fetch_details, to_fetch))

        for app_id, data in zip(to_fetch, fetched):
            row = rows.get(app_id)
            if data is _FAILED:
                if row and not row.not_found and row.data:
                    result[app_id] = row.data  # stale beats nothing
                else:
                    result[app_id] = None
                    failed.add(app_id)
                continue

            data = _slim(data)
            _save_details(app_id, data)
            result[app_id] = data

    return result, failed


def get_details_many(app_ids):
    return load_details(app_ids)[0]


def get_details(app_id):
    """Details dict, or None if Steam says it doesn't exist. Raises if Steam is down and we have nothing."""
    app_id = int(app_id)
    result, failed = load_details([app_id])
    if app_id in failed:
        raise requests.RequestException("Steam is unavailable")
    return result[app_id]


# ---------------------------------------------------------------------------
# Steam review summary (the "Steam rating" on cards)
# ---------------------------------------------------------------------------
def _fetch_rating(app_id):
    try:
        data = get_steam_game_reviews(app_id, num_per_page=1)
    except (requests.RequestException, ValueError):
        return _FAILED

    summary = data.get("summary") or {}
    total = summary.get("total_reviews") or 0
    positive = summary.get("total_positive") or 0
    if not total:
        return {}
    return {
        "score": round(positive / total * 10, 1),
        "label": summary.get("review_score_desc") or "",
        "total_reviews": total,
    }


def get_rating_summaries_many(app_ids):
    """{app_id: {score, label, total_reviews} or None}. Call after load_details."""
    ids = list(dict.fromkeys(int(a) for a in app_ids))
    rows = SteamAppCache.objects.in_bulk(ids)
    now = timezone.now()

    out, to_fetch = {}, []
    for app_id in ids:
        row = rows.get(app_id)
        if row is None or row.not_found:
            out[app_id] = None
        elif row.rating_fetched_at and now - row.rating_fetched_at < RATING_TTL:
            out[app_id] = row.rating_summary or None
        else:
            to_fetch.append(app_id)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(to_fetch))) as pool:
            fetched = list(pool.map(_fetch_rating, to_fetch))

        for app_id, summary in zip(to_fetch, fetched):
            if summary is _FAILED:
                out[app_id] = rows[app_id].rating_summary or None  # stale or none
                continue
            SteamAppCache.objects.filter(pk=app_id).update(
                rating_summary=summary, rating_fetched_at=timezone.now()
            )
            out[app_id] = summary or None

    return out