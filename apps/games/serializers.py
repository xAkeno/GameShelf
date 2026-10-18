
import json
import requests


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
