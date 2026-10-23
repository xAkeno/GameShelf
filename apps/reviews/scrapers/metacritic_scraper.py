
# Metacritic Page Scraper - Print Only

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from urllib.parse import urljoin
import requests

API_KEY = "1MOZgmNFxvmljaQR1X9KAij9Mo4xAY3u"

BASE_URL = "https://backend.metacritic.com/reviews/metacritic"

def get_reviews(game_slug, review_type="user", limit=100):
    offset = 0
    all_reviews = []

    while True:
        url = f"{BASE_URL}/{review_type}/games/{game_slug}/web"

        params = {
            "offset": offset,
            "limit": limit,
            "componentName": (
                "top-user-reviews"
                if review_type == "user"
                else "latest-critic-reviews"
            ),
            "componentType": (
                "UserReviewList"
                if review_type == "user"
                else "CriticReviewList"
            ),
            "apiKey": API_KEY,
        }

        if review_type == "user":
            params["orderBy"] = "date"
            params["orderType"] = "desc"
        else:
            params["sort"] = "date"

        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()["data"]
        reviews = data.get("items", [])

        if not reviews:
            break

        all_reviews.extend(reviews)

        print(
            f"Fetched {len(all_reviews)}"
            f" / {data.get('totalResults', '?')} reviews"
        )

        offset += len(reviews)

        if offset >= data.get("totalResults", offset):
            break

    return all_reviews


if __name__ == "__main__":
    game_slug = "baldurs-gate-3"

    # Use "user" for player reviews or "critic" for critic reviews.
    review_type = "user"

    reviews = get_reviews(game_slug, review_type)

    print(f"\nTotal reviews fetched: {len(reviews)}")

    for index, review in enumerate(reviews, start=1):
        print(f"\n--- Review {index} ---")
        print("Author:", review.get("author") or review.get("username", "N/A"))
        print("Publication:", review.get("publicationName", "N/A"))
        print("Score:", review.get("score", "N/A"))
        print("Date:", review.get("date", "N/A"))
        print("Review:", review.get("quote", "N/A"))


def page_scraper(game_url, game_id):
    # Getting the URL and setting up the soup page to be scraped
    url = urljoin("https://www.metacritic.com", game_url)

    user_agent = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(
            url,
            headers=user_agent,
            timeout=20
        )
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"Request failed: {error}")
        return

    soup = BeautifulSoup(response.text, "html.parser")

    # Getting the Title of the game
    product_title = soup.find("div", class_="product_title")

    if product_title is not None:
        game_title = product_title.find("h1")

        if game_title is not None:
            game_title = game_title.text.strip()
        else:
            game_title = "N/A"
    else:
        # Fallback for the newer page structure
        game_title = soup.find("h1")

        if game_title is not None:
            game_title = game_title.text.strip()
        else:
            print("Game information not found. Metacritic may have changed its HTML.")
            print("HTTP status:", response.status_code)
            print("Page title:", soup.title.get_text(strip=True) if soup.title else "N/A")
            return

    # Getting the Platform the game is on
    platform = product_title.find("span", class_="platform") if product_title else None
    platform = platform.text.strip() if platform else "N/A"

    # Getting the Publisher
    publisher_data = soup.find("li", class_="summary_detail publisher")

    if publisher_data is not None:
        publisher = publisher_data.find("span", class_="data")
        publisher = publisher.get_text(" ", strip=True) if publisher else "N/A"
    else:
        publisher = "N/A"

    # Getting the Release Date
    release_data = soup.find("li", class_="summary_detail release_data")

    if release_data is not None:
        release_element = release_data.find("span", class_="data")

        if release_element:
            release_date = release_element.text.strip()

            try:
                date = datetime.strptime(
                    release_date, "%b %d, %Y"
                ).date()
            except ValueError:
                date = release_date
        else:
            date = "N/A"
    else:
        date = "N/A"

    
    # Getting the Metascore
    meta_score = None

    for element in soup.find_all(class_="c-siteReviewScore_xlarge"):
        classes = element.get("class", [])
        score_text = element.get_text(strip=True)

        if (
            "c-siteReviewScore_green" in classes
            and score_text.isdigit()
        ):
            meta_score = int(score_text)
            break

    # Getting the User Score
    user_score = None

    for element in soup.find_all(class_="c-siteReviewScore_xlarge"):
        classes = element.get("class", [])
        score_text = element.get_text(strip=True)

        if "c-siteReviewScore_tbdUser" in classes:
            user_score = score_text
            break


    # Try newer score elements if the old element is missing
    if meta_score is None:
        for selector in [
            ".c-siteReviewScore",
            ".metascore_w",
        ]:
            score_element = soup.select_one(selector)

            if score_element:
                score_text = score_element.get_text(strip=True)

                try:
                    meta_score = int(score_text)
                    break
                except ValueError:
                    continue

    # Getting the User Score
    user_score = None

    user_wrap = soup.find(
        "div",
        class_="userscore_wrap feature_userscore"
    )

    if user_wrap is not None:
        user_a = user_wrap.find("a", class_="metascore_anchor")
        user_element = user_a.find("div") if user_a else None

        if user_element:
            score_text = user_element.text.strip()

            try:
                user_score = float(score_text)
            except ValueError:
                pass

    if user_score is None:
        user_element = soup.select_one(
            ".c-siteReviewScore_user"
        )

        if user_element:
            try:
                user_score = float(
                    user_element.get_text(strip=True)
                )
            except ValueError:
                pass

    # Getting the Developer
    dev_data = soup.find("li", class_="summary_detail developer")

    if dev_data is not None:
        developer_element = dev_data.find("span", class_="data")
        developer = (
            developer_element.get_text(" ", strip=True)
            if developer_element else "N/A"
        )
    else:
        developer = "N/A"

    # Getting the Genres
    genre_set = set()

    genre_data = soup.find(
        "li",
        class_="summary_detail product_genre"
    )

    if genre_data is not None:
        for item in genre_data.find_all("span", class_="data"):
            genre = item.text.strip()

            if genre:
                genre_set.add(genre)

    # Getting the Rating
    rating_data = soup.find(
        "li",
        class_="summary_detail product_rating"
    )

    if rating_data is not None:
        rating_element = rating_data.find("span", class_="data")
        rating = rating_element.text.strip() if rating_element else "N/A"
    else:
        rating = "N/A"

    # Getting the Cover Art
    product_image = soup.find(
        "div",
        class_="product_image large_image must_play"
    )

    image = (
        product_image.find("img", class_="product_image large_image")
        if product_image else None
    )

    if image is not None:
        source = image.get("src") or image.get("data-src") or "N/A"
    else:
        image_meta = soup.find("meta", property="og:image")
        source = image_meta.get("content") if image_meta else "N/A"

    if source != "N/A":
        source = urljoin(url, source)

    # Printing the scraped data
    print("\n" + "=" * 50)
    print(f"Game ID:     {game_id}")
    print(f"Title:       {game_title}")
    print(f"Developer:   {developer}")
    print(f"Publisher:   {publisher}")
    print(f"Rating:      {rating}")
    print(f"Metascore:   {meta_score if meta_score is not None else 'N/A'}")
    print(f"User Score:  {user_score if user_score is not None else 'N/A'}")
    print(f"Release:     {date}")
    print(f"Platform:    {platform}")
    print(f"Genres:      {', '.join(sorted(genre_set)) if genre_set else 'N/A'}")
    print(f"Cover Image: {source}")
    print(f"URL:         {url}")
    print("=" * 50)


if __name__ == "__main__":
    page_scraper(
        "https://www.metacritic.com/game/baldurs-gate-3/",
        1
    )
