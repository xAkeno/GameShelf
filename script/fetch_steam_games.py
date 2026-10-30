
import json
import time
from pathlib import Path

import requests
from decouple import config


API_KEY = config("STEAM_API_KEY", default="")

if not API_KEY:
    raise RuntimeError("STEAM_API_KEY is missing from your .env file.")

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
OUTPUT_FILE = DATA_DIR / "steam_games.json"

APP_LIST_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
DETAILS_URL = "https://store.steampowered.com/api/appdetails"

# Adjust these if Steam starts returning rate limits.
REQUEST_DELAY = 1.0
SAVE_EVERY = 250
MAX_RETRIES = 4

DATA_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()


def save_games(games_by_id):
    """Atomically save the current dataset."""
    temp_file = OUTPUT_FILE.with_suffix(".tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(
            list(games_by_id.values()),
            file,
            ensure_ascii=False,
            indent=2,
        )

    temp_file.replace(OUTPUT_FILE)


def load_existing_games():
    """Load previous results so the script can resume."""
    if not OUTPUT_FILE.exists():
        return {}

    with OUTPUT_FILE.open("r", encoding="utf-8") as file:
        existing = json.load(file)

    return {
        str(game["appid"]): game
        for game in existing
        if game.get("appid") is not None
    }


def fetch_app_list(games_by_id):
    """Fetch all app IDs and names using Steam pagination."""
    last_appid = 0

    while True:
        params = {
            "key": API_KEY,
            "max_results": 50000,
            "last_appid": last_appid,
            "include_games": True,
            "include_dlc": False,
        }

        response = session.get(
            APP_LIST_URL,
            params=params,
            timeout=60,
        )
        response.raise_for_status()

        payload = response.json().get("response", {})
        apps = payload.get("apps", [])

        for app in apps:
            appid = app.get("appid")
            name = app.get("name")

            if appid and isinstance(name, str) and name.strip():
                key = str(int(appid))

                # Preserve metadata already fetched on earlier runs.
                if key not in games_by_id:
                    games_by_id[key] = {
                        "appid": int(appid),
                        "name": name.strip(),
                        "genres": [],
                        "categories": [],
                        "platforms": [],
                        "description": "",
                        "developers": [],
                        "publishers": [],
                        "release_date": None,
                        "price_overview": None,
                        "metadata_status": "pending",
                    }
                else:
                    games_by_id[key]["name"] = name.strip()

        new_last_appid = int(payload.get("last_appid", 0))
        have_more = payload.get("have_more_results", False)

        save_games(games_by_id)

        print(
            f"App list: {len(games_by_id):,} games; "
            f"cursor: {new_last_appid}"
        )

        if (
            not have_more
            or not apps
            or new_last_appid <= last_appid
        ):
            break

        last_appid = new_last_appid
        time.sleep(0.2)


def fetch_game_details(appid):
    """Fetch metadata for one Steam app, with retries."""
    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(
                DETAILS_URL,
                params={
                    "appids": appid,
                    "cc": "ph",
                    "l": "english",
                },
                timeout=30,
            )

            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After")

                try:
                    delay = float(retry_after) if retry_after else 2 ** (attempt + 1)
                except ValueError:
                    delay = 2 ** (attempt + 1)

                time.sleep(min(delay, 120))
                continue

            response.raise_for_status()

            result = response.json().get(str(appid), {})

            if not result.get("success"):
                return None

            data = result.get("data", {})
            platforms_data = data.get("platforms", {})
            release_data = data.get("release_date", {})

            return {
                "appid": int(appid),
                "name": data.get("name", ""),
                "genres": [
                    item.get("description", "")
                    for item in data.get("genres", [])
                    if item.get("description")
                ],
                "categories": [
                    item.get("description", "")
                    for item in data.get("categories", [])
                    if item.get("description")
                ],
                "platforms": [
                    platform
                    for platform, supported in platforms_data.items()
                    if supported
                ],
                "description": data.get("short_description", ""),
                "developers": data.get("developers", []),
                "publishers": data.get("publishers", []),
                "release_date": release_data.get("date") or None,
                "price_overview": data.get("price_overview"),
                "metadata_status": "complete",
            }

        except (requests.RequestException, ValueError) as exc:
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError(
                    f"Failed to fetch app {appid} after "
                    f"{MAX_RETRIES} attempts: {exc}"
                ) from exc

            time.sleep(2 ** (attempt + 1))

    return None


def enrich_games(games_by_id):
    """Fetch missing details without repeating completed requests."""
    processed = 0
    unavailable = 0

    # Snapshot the IDs so updates to records are safe.
    appids = list(games_by_id.keys())
    total = len(appids)

    try:
        for index, key in enumerate(appids, start=1):
            game = games_by_id[key]

            if game.get("metadata_status") in {"complete", "unavailable"}:
                continue

            details = fetch_game_details(key)

            if details is None:
                # Avoid repeatedly requesting unavailable app details.
                game["metadata_status"] = "unavailable"
                unavailable += 1
            else:
                game.update(details)

            processed += 1

            if processed % SAVE_EVERY == 0:
                save_games(games_by_id)

            print(
                f"Details: {index:,}/{total:,} | "
                f"{game.get('name', key)} | "
                f"{game['metadata_status']}"
            )

            time.sleep(REQUEST_DELAY)

    finally:
        # Preserve progress if a request fails or the script is interrupted.
        save_games(games_by_id)

    print(f"Newly processed: {processed:,}")
    print(f"Unavailable details: {unavailable:,}")


def main():
    games_by_id = load_existing_games()

    print(f"Previously saved: {len(games_by_id):,} games")

    fetch_app_list(games_by_id)

    print("\nFetching missing game metadata...")
    enrich_games(games_by_id)

    print(f"\nFinished. Dataset: {OUTPUT_FILE}")
    print(f"Total games: {len(games_by_id):,}")


if __name__ == "__main__":
    main()
