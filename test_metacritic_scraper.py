
import unittest

from bs4 import BeautifulSoup

from apps.reviews.scrapers.metacritic_scraper import (
    get_text,
    find_text,
    parse_number,
)

class TestMetacriticScraper(unittest.TestCase):

    def test_get_text(self):
        soup = BeautifulSoup(
            '<div class="score">85</div>',
            "html.parser",
        )

        self.assertEqual(
            get_text(soup.select_one(".score")),
            "85",
        )

    def test_get_text_missing_element(self):
        self.assertEqual(get_text(None), "N/A")

    def test_find_text(self):
        soup = BeautifulSoup(
            "<h1>Counter-Strike 2</h1>",
            "html.parser",
        )

        self.assertEqual(
            find_text(soup, ["h1"]),
            "Counter-Strike 2",
        )

    def test_find_text_missing_element(self):
        soup = BeautifulSoup("<div></div>", "html.parser")

        self.assertEqual(
            find_text(soup, ["h1"]),
            "N/A",
        )

    def test_parse_integer_score(self):
        self.assertEqual(parse_number("85"), 85)

    def test_parse_float_score(self):
        self.assertEqual(
            parse_number("8.5", number_type=float),
            8.5,
        )

    def test_parse_tbd_score(self):
        self.assertEqual(parse_number("tbd"), 0)

    def test_parse_invalid_score(self):
        self.assertEqual(parse_number("invalid"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
