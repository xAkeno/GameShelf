import requests

STEAM_APP_LIST_URL = (
    "https://api.steampowered.com/"
    "IStoreService/GetAppList/v1/"
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
            "max_results": min(max(limit, 1), 50000),
        },
        timeout=20,
    )

    response.raise_for_status()
    payload = response.json().get("response", {})

    return {
        "games": payload.get("apps", []),
        "last_appid": payload.get("last_appid"),
        "have_more_results": payload.get("have_more_results", False),
    }