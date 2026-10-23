
import json

from apps.reviews.scrapers.reddit_scraper import SeleniumScraper


scraper = SeleniumScraper(headless=False)

try:
    links = scraper.scrape_search(
        game_name="Counter-Strike 2",
        scroll_n_times=3,
    )

    reviews = []

    for url in links[:10]:
        item = scraper.scrape_post(url)

        if item and (item["title"] or item["body"]):
            reviews.append(item)

    result = {
        "source": "reddit",
        "kind": "reviews",
        "summary": {
            "score": None,
            "label": "Community discussions",
            "count": len(reviews),
            "url": (
                "https://www.reddit.com/search/?q="
                "Counter-Strike%202%20review"
            ),
        },
        "items": reviews,
        "next_cursor": None,
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))

    with open(
        "reddit_reviews.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            result,
            file,
            indent=2,
            ensure_ascii=False,
        )

finally:
    scraper.close()
