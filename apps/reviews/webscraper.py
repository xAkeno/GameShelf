"""
Third-party review sources, normalised to one response shape:

{
  "source": "reddit",
  "kind": "reviews" | "summary",
  "summary": {"score": 0-10 or None, "label": str|None, "count": int|None, "url": str|None},
  "items": [{"id", "author", "title", "body", "positive", "score", "meta", "url", "created_at"}],
  "next_cursor": str | None,
}

Only sources with a usable public API live here. Sites without one (Xbox,
OpenCritic, IGN ...) are "link-out" cards handled by the frontend.
"""
from datetime import datetime, timezone as dt_timezone
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.cache import cache

from apps.games.serializers import get_steam_game_details, get_steam_game_reviews

SUPPORTED_SOURCES = ("steam", "reddit", "metacritic")
CACHE_SECONDS = 60 * 10

REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"


def _iso(unix_seconds):
    if not unix_seconds:
        return None
    return datetime.fromtimestamp(unix_seconds, tz=dt_timezone.utc).isoformat()


def _details(app_id):
    key = f"steam:details:{app_id}"
    details = cache.get(key)
    if details is None:
        details = get_steam_game_details(app_id)
        if details:
            cache.set(key, details, 60 * 60)
    return details or {}


def _steam(app_id, cursor, limit):
    data = get_steam_game_reviews(app_id, num_per_page=limit, cursor=cursor)
    summary = data.get("summary", {})
    total = summary.get("total_reviews") or 0
    positive = summary.get("total_positive") or 0
    store_url = f"https://store.steampowered.com/app/{app_id}/#app_reviews_hash"

    items = []
    for review in data.get("reviews", []):
        hours = round((review.get("author", {}).get("playtime_forever") or 0) / 60)
        items.append(
            {
                "id": str(review.get("recommendationid")),
                "author": "Steam player",
                "title": None,
                "body": review.get("review", ""),
                "positive": review.get("voted_up"),
                "score": review.get("votes_up"),
                "meta": f"{hours:,} h on record",
                "url": None,
                "created_at": _iso(review.get("timestamp_created")),
            }
        )

    return {
        "kind": "reviews",
        "summary": {
            "score": round(positive / total * 10, 1) if total else None,
            "label": summary.get("review_score_desc"),
            "count": total,
            "url": store_url,
        },
        "items": items,
        "next_cursor": data.get("cursor") if len(items) >= limit else None,
    }


def _reddit(app_id, cursor, limit):
    name = _details(app_id).get("name")
    if not name:
        return {"kind": "reviews", "summary": {}, "items": [], "next_cursor": None}

    params = {
        "q": f'"{name}" review',
        "sort": "relevance",
        "t": "all",
        "type": "link",
        "limit": limit,
        "include_over_18": "off",
        "raw_json": 1,
    }
    if cursor and cursor != "*":
        params["after"] = cursor

    response = requests.get(
        REDDIT_SEARCH_URL,
        params=params,
        headers={
            # Reddit rejects requests without a descriptive User-Agent.
            "User-Agent": getattr(
                settings, "REDDIT_USER_AGENT", "GameShelf/1.0 (review aggregator)"
            )
        },
        timeout=15,
    )
    response.raise_for_status()
    listing = response.json().get("data", {})

    items = []
    for child in listing.get("children", []):
        post = child.get("data", {})
        body = (post.get("selftext") or "").strip()
        items.append(
            {
                "id": post.get("id"),
                "author": f"u/{post.get('author')}" if post.get("author") else "Reddit user",
                "title": post.get("title"),
                "body": body[:600] + ("…" if len(body) > 600 else ""),
                "positive": None,
                "score": post.get("ups"),
                "meta": f"r/{post.get('subreddit')} • {post.get('num_comments', 0):,} comments",
                "url": f"https://www.reddit.com{post.get('permalink', '')}",
                "created_at": _iso(post.get("created_utc")),
            }
        )

    return {
        "kind": "reviews",
        "summary": {
            "score": None,
            "label": None,
            "count": None,
            "url": f"https://www.reddit.com/search/?q={quote(name + ' review')}",
        },
        "items": items,
        "next_cursor": listing.get("after"),
    }


def _metacritic(app_id, cursor, limit):
    """Steam's store data includes the Metascore and the Metacritic page URL."""
    details = _details(app_id)
    meta = details.get("metacritic") or {}
    score = meta.get("score")

    return {
        "kind": "summary",
        "summary": {
            "score": round(score / 10, 1) if score is not None else None,
            "label": "Metascore (critics)",
            "count": None,
            "url": meta.get("url"),
        },
        "items": [],
        "next_cursor": None,
    }


_PROVIDERS = {"steam": _steam, "reddit": _reddit, "metacritic": _metacritic}


def fetch_external_reviews(source, app_id, cursor="*", limit=5):
    """Raises requests.RequestException when the upstream site fails."""
    cache_key = f"external:{source}:{app_id}:{limit}:{cursor}"
    result = cache.get(cache_key)

    if result is None:
        result = _PROVIDERS[source](app_id, cursor, limit)
        result = {"source": source, **result}
        cache.set(cache_key, result, CACHE_SECONDS)

    return result