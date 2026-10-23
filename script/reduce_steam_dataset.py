
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

INPUT_FILE = BASE_DIR / "data" / "steam_games.json"
OUTPUT_FILE = BASE_DIR / "data" / "steam_games_reduced.json"

KEEP_FIELDS = {
    "name",
    "release_date",
    "price",
    "windows",
    "mac",
    "linux",
    "metacritic_score",
    "positive",
    "negative",
    "average_playtime_forever",
    "estimated_owners",
    "developers",
    "publishers",
    "genres",
    "categories",
    "short_description",
    "tags",
}


def reduce_dataset():
    if not INPUT_FILE.exists():
        print(f"Input file not found: {INPUT_FILE}")
        return

    print("Reading Steam dataset...")

    with INPUT_FILE.open("r", encoding="utf-8") as file:
        games = json.load(file)

    reduced_games = {}

    if isinstance(games, dict):
        iterator = games.items()
    elif isinstance(games, list):
        iterator = (
            (str(game.get("appid", game.get("id", ""))), game)
            for game in games
            if isinstance(game, dict)
        )
    else:
        print("Unsupported JSON format. Expected a list or dictionary.")
        return

    for appid, game in iterator:
        if not isinstance(game, dict):
            continue

        if not appid:
            continue

        reduced_game = {
            key: game[key]
            for key in KEEP_FIELDS
            if key in game
        }

        if not reduced_game.get("name"):
            continue

        reduced_games[str(appid)] = reduced_game

    print("Saving reduced dataset...")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            reduced_games,
            file,
            ensure_ascii=False,
            indent=4,
        )

    original_size = INPUT_FILE.stat().st_size / (1024 * 1024)
    reduced_size = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    saved_size = original_size - reduced_size
    saved_percent = (
        saved_size / original_size * 100
        if original_size > 0
        else 0
    )

    print("\nDataset reduction complete!")
    print(f"Games kept:     {len(reduced_games):,}")
    print(f"Original size:  {original_size:.2f} MB")
    print(f"Reduced size:   {reduced_size:.2f} MB")
    print(f"Space saved:    {saved_size:.2f} MB ({saved_percent:.1f}%)")
    print(f"Output file:    {OUTPUT_FILE}")


if __name__ == "__main__":
    reduce_dataset()
