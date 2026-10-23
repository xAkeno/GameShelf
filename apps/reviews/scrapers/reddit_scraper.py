
import json
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)


class SeleniumScraper:
    def __init__(self, headless=True):
        self.driver = None
        self.page = ""
        self.links = []
        self.headless = headless

    def setup_chrome_browser(self):
        options = webdriver.ChromeOptions()

        options.add_argument("--disable-notifications")
        options.add_argument("--window-size=1440,1000")

        if self.headless:
            options.add_argument("--headless=new")

        # Selenium Manager attempts to locate/manage ChromeDriver.
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(30)

    def collect_links(self, page, scroll_n_times=2):
        if scroll_n_times < 0:
            raise ValueError(
                "scroll_n_times must be greater than or equal to 0"
            )

        self.page = page
        self.links = []

        if self.driver is None:
            self.setup_chrome_browser()

        try:
            self.driver.get(page)

            WebDriverWait(self.driver, 15).until(
                lambda driver: driver.find_elements(By.TAG_NAME, "body")
            )

            for _ in range(scroll_n_times):
                self.driver.execute_script(
                    "window.scrollTo(0, document.body.scrollHeight);"
                )
                time.sleep(1)

            # Collect links from the currently rendered page.
            elements = self.driver.find_elements(
                By.CSS_SELECTOR,
                'a[href*="/comments/"]',
            )

            seen = set()

            for element in elements:
                href = element.get_attribute("href")

                if not href:
                    continue

                parsed = urlparse(href)

                if (
                    parsed.hostname
                    and parsed.hostname.endswith("reddit.com")
                    and "/comments/" in parsed.path
                ):
                    clean_url = (
                        f"https://www.reddit.com{parsed.path}"
                    )

                    if clean_url not in seen:
                        seen.add(clean_url)
                        self.links.append(clean_url)

        except TimeoutException:
            print("Page loading timed out.")
        except WebDriverException as exc:
            print(f"Browser error: {exc}")

        print(f"Collected {len(self.links)} links")
        return self.links

    def scrape_search(self, game_name, scroll_n_times=2):
        query = quote_plus(f'"{game_name}" review')
        search_url = (
            f"https://www.reddit.com/search/?q={query}&type=posts"
        )

        return self.collect_links(search_url, scroll_n_times)

    def scrape_post(self, url):
        if self.driver is None:
            self.setup_chrome_browser()

        try:
            self.driver.get(url)

            WebDriverWait(self.driver, 15).until(
                lambda driver: driver.find_elements(By.TAG_NAME, "body")
            )

            title = ""
            body = ""
            author = "Reddit user"
            score = None
            comments = 0
            created_at = None

            # Prefer semantic elements when available.
            title_elements = self.driver.find_elements(
                By.CSS_SELECTOR,
                "shreddit-post[post-title]",
            )

            if title_elements:
                title = (
                    title_elements[0].get_attribute("post-title") or ""
                )
                author = (
                    title_elements[0].get_attribute("author")
                    or author
                )

                raw_score = title_elements[0].get_attribute("score")
                try:
                    score = int(raw_score)
                except (ValueError, TypeError):
                    pass

                raw_comments = title_elements[0].get_attribute(
                    "comment-count"
                )
                try:
                    comments = int(raw_comments)
                except (ValueError, TypeError):
                    pass

                raw_created = title_elements[0].get_attribute(
                    "created-timestamp"
                )
                if raw_created:
                    created_at = raw_created

            if not title:
                titles = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    "h1",
                )
                if titles:
                    title = titles[0].text.strip()

            # Read the post's visible text where available.
            body_elements = self.driver.find_elements(
                By.CSS_SELECTOR,
                "shreddit-post [slot='text-body']",
            )

            if body_elements:
                body = body_elements[0].text.strip()

            if not body:
                body_elements = self.driver.find_elements(
                    By.CSS_SELECTOR,
                    '[data-testid="post-content"]',
                )
                if body_elements:
                    body = body_elements[0].text.strip()

            if created_at:
                try:
                    parsed = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00")
                    )
                    created_at = parsed.astimezone(
                        timezone.utc
                    ).isoformat()
                except ValueError:
                    pass

            return {
                "id": url.rstrip("/").split("/")[-1],
                "author": author,
                "title": title or None,
                "body": body[:600],
                "positive": None,
                "score": score,
                "meta": f"{comments:,} comments",
                "url": url,
                "created_at": created_at,
            }

        except (TimeoutException, WebDriverException) as exc:
            print(f"Could not read post {url}: {exc}")
            return None

    def reddit_data_to_dict(self, script_data=None):
        """Parse JSON strings extracted from page scripts."""
        pure_dicts = []

        for data in script_data or []:
            try:
                start = data.index("{")
                end = data.rfind("}") + 1

                if start >= 0 and end > start:
                    pure_dicts.append(
                        json.loads(data[start:end])
                    )
            except (ValueError, json.JSONDecodeError):
                continue

        return pure_dicts

    def close(self):
        if self.driver is not None:
            self.driver.quit()
            self.driver = None
