"""Parsing des flux Pepper (API v1 conservée via scraper.fetch)."""

import pytest

from scraper.fetch import classify_marketplace, find_original_price, parse_feed, parse_price
from scraper.sources.pepper_source import has_glitch_keyword, parse_thread_page

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:pepper="http://www.pepper.com/rss" xmlns:media="http://search.yahoo.com/mrss/" version="2.0"><channel>
<item><category><![CDATA[High-Tech]]></category><pepper:merchant name="Amazon.de" price="1.299,99&#8364;"/>
<media:content medium="image" url="https://example.com/a.jpg"/>
<title><![CDATA[TV OLED 65"]]></title>
<description><![CDATA[<strong>1.299,99&#8364; - Amazon.de</strong><br />Erreur de prix, au lieu de 2.499,00&#8364;</p>]]></description>
<link>https://www.dealabs.com/bons-plans/tv-oled-65-123456</link>
<pubDate>Sun, 04 Oct 2026 17:02:41 +0200</pubDate>
<guid>https://www.dealabs.com/bons-plans/tv-oled-65-123456</guid></item>
<item><pepper:merchant name="Fnac" price="12&#8364;"/>
<title><![CDATA[Chaussettes rigolotes]]></title>
<description><![CDATA[<strong>12&#8364; - Fnac</strong><br />Super chaussettes</p>]]></description>
<link>https://www.dealabs.com/bons-plans/chaussettes-777</link>
<pubDate>Sun, 04 Oct 2026 17:02:41 +0200</pubDate></item>
</channel></rss>"""


@pytest.mark.parametrize("text,expected", [
    ("0,01€", (0.01, "EUR")),
    ("1.299,99€", (1299.99, "EUR")),
    ("1.299€", (1299.0, "EUR")),
    ("2€", (2.0, "EUR")),
    ("£1.99", (1.99, "GBP")),
    ("£1,299.50", (1299.5, "GBP")),
    ("$12.50", (12.5, "USD")),
    ("1 299,99 €", (1299.99, "EUR")),
    ("GRATUIT", (0.0, None)),
    (None, (None, None)),
])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_classify_marketplace():
    assert classify_marketplace("Amazon.pl", "") == "Amazon"
    assert classify_marketplace("Cdiscount", "") == "Cdiscount"
    assert classify_marketplace(None, "Erreur chez Fnac") == "Fnac"
    assert classify_marketplace("Petit Shop", "") == "Autre"


def test_find_original_price():
    assert find_original_price("statt 18,90€ jetzt", 2.0) == 18.9
    assert find_original_price("au lieu de 1€", 2.0) is None


def test_parse_feed_glitch_stream():
    deal, socks = parse_feed(FEED, {"id": "dealabs", "country": "FR"})
    assert deal["id"] == "dealabs:123456"
    assert deal["marketplace"] == "Amazon"
    assert deal["price"] == 1299.99
    assert deal["original_price"] == 2499.0
    assert deal["discount"] == 48
    assert deal["image"] == "https://example.com/a.jpg"
    assert deal["published"] == "2026-10-04T15:02:41+00:00"
    assert "Amazon.de" not in deal["description"]
    assert deal["stream"] == "glitch" and deal["glitch_keyword"] is True
    assert socks["id"] == "dealabs:777"


def test_parse_feed_fresh_stream_flags_keywords():
    deal, socks = parse_feed(FEED, {"id": "dealabs", "country": "FR"}, stream="fresh")
    assert deal["glitch_keyword"] is True          # « Erreur de prix » dans la description
    assert socks["glitch_keyword"] is False


@pytest.mark.parametrize("text", ["Erreur de prix sur la TV", "BUG DE PRIX", "Preisfehler bei Otto",
                                  "Price glitch at Argos", "prix fou chez Darty"])
def test_glitch_keywords(text):
    assert has_glitch_keyword(text)


def test_no_glitch_keyword():
    assert not has_glitch_keyword("Promo classique -20 % sur les casques")


PAGE = (
    '{"threadId":"111","temperature":12.5,"nextBestPrice":99}'
    ',"isTrending":false,"nextBestPrice":449,"percentage":0,"price":278.38,"linkHost":"www.fnac.com",'
    '"temperature":393.78,"temperatureLevel":"Hot2","threadId":"3436986","issueReported":false,'
    '"commentCount":96,"isExpired":true}'
)


def test_parse_thread_page():
    info = parse_thread_page(PAGE, "3436986")
    assert info == {"temperature": 393.78, "next_best_price": 449.0, "expired": True, "link_host": "www.fnac.com"}


def test_parse_thread_page_does_not_leak_neighbour():
    # Annonces voisines : threadId d'abord, puis leurs champs ; l'annonce principale : l'inverse.
    neighbour = '{"threadId":"111","nextBestPrice":99,"temperature":12.5,"title":"' + 'x' * 500 + '"}'
    page = neighbour + "," + '{"threadId":"222","issueReported":false}'
    assert parse_thread_page(page, "222") == {}
    assert parse_thread_page(page, "999") == {}
