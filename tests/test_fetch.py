import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))

from fetch import classify_marketplace, find_original_price, parse_feed, parse_price  # noqa: E402

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:pepper="http://www.pepper.com/rss" xmlns:media="http://search.yahoo.com/mrss/" version="2.0"><channel>
<item><category><![CDATA[High-Tech]]></category><pepper:merchant name="Amazon.de" price="1.299,99&#8364;"/>
<media:content medium="image" url="https://example.com/a.jpg"/>
<title><![CDATA[TV OLED 65"]]></title>
<description><![CDATA[<strong>1.299,99&#8364; - Amazon.de</strong><br />Erreur de prix, au lieu de 2.499,00&#8364;</p>]]></description>
<link>https://www.dealabs.com/bons-plans/tv-oled-65-123456</link>
<pubDate>Sun, 04 Oct 2026 17:02:41 +0200</pubDate>
<guid>https://www.dealabs.com/bons-plans/tv-oled-65-123456</guid></item>
</channel></rss>"""


class ParsePriceTest(unittest.TestCase):
    def test_formats(self):
        cases = {
            "0,01€": (0.01, "EUR"),
            "1.299,99€": (1299.99, "EUR"),
            "1.299€": (1299.0, "EUR"),
            "2€": (2.0, "EUR"),
            "£1.99": (1.99, "GBP"),
            "£1,299.50": (1299.5, "GBP"),
            "$12.50": (12.5, "USD"),
            "1 299,99 €": (1299.99, "EUR"),
            "GRATUIT": (0.0, None),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_price(text), expected)

    def test_missing(self):
        self.assertEqual(parse_price(None), (None, None))


class ClassifyTest(unittest.TestCase):
    def test_marketplaces(self):
        self.assertEqual(classify_marketplace("Amazon.pl", ""), "Amazon")
        self.assertEqual(classify_marketplace("Cdiscount", ""), "Cdiscount")
        self.assertEqual(classify_marketplace(None, "Erreur chez Fnac"), "Fnac")
        self.assertEqual(classify_marketplace("Petit Shop", ""), "Autre")


class OriginalPriceTest(unittest.TestCase):
    def test_finds_higher_price(self):
        self.assertEqual(find_original_price("statt 18,90€ jetzt", 2.0), 18.9)
        self.assertIsNone(find_original_price("au lieu de 1€", 2.0))


class ParseFeedTest(unittest.TestCase):
    def test_item(self):
        source = {"id": "dealabs", "country": "FR"}
        (deal,) = parse_feed(FEED, source)
        self.assertEqual(deal["id"], "dealabs:123456")
        self.assertEqual(deal["marketplace"], "Amazon")
        self.assertEqual(deal["price"], 1299.99)
        self.assertEqual(deal["original_price"], 2499.0)
        self.assertEqual(deal["discount"], 48)
        self.assertEqual(deal["image"], "https://example.com/a.jpg")
        self.assertEqual(deal["published"], "2026-10-04T15:02:41+00:00")
        self.assertNotIn("Amazon.de", deal["description"])


if __name__ == "__main__":
    unittest.main()
