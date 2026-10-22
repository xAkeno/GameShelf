import json
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from html import unescape

import requests

STEAM_PAGE_SIZE = 25      # Steam's real page size; `start` must be a multiple of it
SEARCH_MAX_CHUNKS = 8     # when searching by title, scan at most 8 * 25 = 200 results

_ROW_RE = re.compile(r'(<a\b[^>]*\bsearch_result_row\b[^>]*>)(.*?)</a>', re.S)
_APP_ID_RE = re.compile(r'data-ds-appid="(\d+)"')   # bundles ("123,456") don't match
_TITLE_RE = re.compile(r'<span class="title">(.*?)</span>', re.S)

STEAM_APP_LIST_URL = (
    "https://api.steampowered.com/"
    "IStoreService/GetAppList/v1/"
)

STEAM_GAME_DETAILS_URL = (
    "https://store.steampowered.com/api/appdetails/"
)

STEAM_GAME_REVIEWS_URL = (
    "https://api.steampowered.com/"
    "IUserReviewsService/GetAppReviews/v1/"
)

STEAM_STORE_SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
)
_APP_ID_RE = re.compile(r'data-ds-appid="(\d+)"')


def _fetch_search_chunk(term, tag_id, sort_by, platform, start, country="ph"):
    """One raw Steam page (up to 25 rows). Returns ([{appid, name}], total_count).

    A row with no single app id (bundle) keeps appid=None so indexes stay aligned.
    """
    params = {
        "term": term,
        "category1": 998,  # games only
        "sort_by": sort_by,
        "start": start,
        "count": STEAM_PAGE_SIZE,
        "cc": country,
        "l": "english",
        "infinite": 1,
        "supportedlang": "english",
        "ndl": 1,
    }
    if tag_id:
        params["tags"] = int(tag_id)
    if platform in {"win", "mac", "linux"}:
        params["os"] = platform

    response = requests.get(
        STEAM_STORE_SEARCH_URL,
        params=params,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    response.raise_for_status()

    data = response.json()
    html = data.get("results_html") or ""

    rows = []
    for m in _ROW_RE.finditer(html):
        tag, inner = m.group(1), m.group(2)
        id_match = _APP_ID_RE.search(tag)
        title_match = _TITLE_RE.search(inner)
        rows.append(
            {
                "appid": int(id_match.group(1)) if id_match else None,
                "name": unescape(title_match.group(1)).strip() if title_match else "",
            }
        )

    return rows, int(data.get("total_count", 0))


def search_steam_store(
    term: str = "",
    tag_id=None,
    sort_by: str = "Reviews_DESC",
    platform: str = "",
    start: int = 0,
    count: int = 24,
    country: str = "ph",
):
    """Plain browsing (no search text). Returns (appids for rows start..start+count, total)."""
    count = min(max(int(count), 1), 100)
    start = max(int(start), 0)
    end = start + count

    first_chunk = (start // STEAM_PAGE_SIZE) * STEAM_PAGE_SIZE
    chunk_start = first_chunk
    rows, total = [], 0

    while chunk_start < end:
        chunk, total = _fetch_search_chunk(
            term, tag_id, sort_by, platform, chunk_start, country
        )
        rows.extend(chunk)
        if len(chunk) < STEAM_PAGE_SIZE:
            break
        chunk_start += STEAM_PAGE_SIZE

    window = rows[start - first_chunk : start - first_chunk + count]

    appids = []
    for r in window:
        if r["appid"] and r["appid"] not in appids:
            appids.append(r["appid"])

    return appids, total


# --- title search -----------------------------------------------------------
def _norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _title_matches(name, query):
    """Loose match: case/punctuation/accent-insensitive, every word must appear."""
    n, q = _norm(name), _norm(query)
    if not q:
        return True
    if all(word in n for word in q.split()):
        return True
    return q.replace(" ", "") in n.replace(" ", "")   # "dyinglight" -> Dying Light


def search_steam_titles(term, tag_id=None, platform="", country="ph"):
    """Games whose TITLE contains the search words, in Steam relevance order.

    Returns [{appid, name}, ...]. Scans up to SEARCH_MAX_CHUNKS * 25 results.
    """
    first, total = _fetch_search_chunk(term, tag_id, "_ASC", platform, 0, country)
    rows = list(first)

    chunks = min(math.ceil(total / STEAM_PAGE_SIZE), SEARCH_MAX_CHUNKS)
    if chunks > 1:
        starts = [i * STEAM_PAGE_SIZE for i in range(1, chunks)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            for chunk, _ in executor.map(
                lambda s: _fetch_search_chunk(term, tag_id, "_ASC", platform, s, country),
                starts,
            ):
                rows.extend(chunk)

    seen, matches = set(), []
    for r in rows:
        if r["appid"] and r["appid"] not in seen and _title_matches(r["name"], term):
            seen.add(r["appid"])
            matches.append(r)
    return matches

def get_steam_game_list(api_key, last_appid=0, limit=12):
    response = requests.get(
        STEAM_APP_LIST_URL,
        headers={"x-webapi-key": api_key},
        params={
            "include_games": True,
            "include_dlc": False,
            "include_software": False,
            "include_videos": False,
            "include_hardware": False,
            "last_appid": last_appid,
            "max_results": min(max(int(limit), 1), 100),
        },
        timeout=20,
    )

    response.raise_for_status()

    payload = response.json().get("response", {})

    return {
        "games": payload.get("apps", []),
        "last_appid": payload.get("last_appid"),
        "have_more_results": payload.get(
            "have_more_results",
            False,
        ),
    }


def get_steam_game_details(app_id: int):
    response = requests.get(
        STEAM_GAME_DETAILS_URL,
        params={
            "appids": app_id,
            "cc": "ph",
            "l": "english",
        },
        timeout=15,
    )

    response.raise_for_status()

    result = response.json().get(str(app_id), {})

    if not result.get("success"):
        return None

    return result.get("data")


def get_steam_game_reviews(
    app_id: int,
    num_per_page: int = 10,
    cursor: str = "*",
    language: str = "english",
):
    params = {
        "input_json": json.dumps({
            "appid": app_id,
            "filter": 1,
            "languages": [language],
            "review_type": 0,
            "purchase_type": 1,
            "num_per_page": min(
                max(int(num_per_page), 1),
                100,
            ),
            "cursor": cursor,
        })
    }

    response = requests.get(
        STEAM_GAME_REVIEWS_URL,
        params=params,
        timeout=15,
    )

    response.raise_for_status()

    data = response.json()
    result = data.get("response", data)

    return {
        "summary": result.get("query_summary", {}),
        "reviews": result.get("reviews", []),
        "cursor": result.get("cursor", "*"),
        "total_matching": result.get("total_matching", 0),
    }