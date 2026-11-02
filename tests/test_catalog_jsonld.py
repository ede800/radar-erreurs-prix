"""Catalogue de prix de référence, résolution JSON-LD et liens marchands du flux."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from scraper.glitch_engine import analyze
from scraper.price_verifier import PriceVerifier, parse_product_jsonld, resolvable, resolve_product
from scraper.reference_catalog import lookup
from scraper.sources.pepper_source import merchant_links, parse_feed

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def make(**fields):
    deal = {"id": "mydealz:1", "title": "x", "merchant": "MediaMarkt", "price": 100.0, "currency": "EUR",
            "description": "", "published": (NOW - timedelta(hours=1)).isoformat()}
    deal.update(fields)
    return deal


# --- Catalogue ------------------------------------------------------------------------

@pytest.mark.parametrize("title,name,price", [
    ("Zotac Gaming GeForce RTX 5070 Twin Edge OC Grafikkarte 12GB", "RTX 5070", 620),
    ("MSI GeForce RTX 5070 Ti Ventus 3X", "RTX 5070 Ti", 880),
    ("Sapphire Pulse RX 9070 XT 16 Go", "RX 9070 XT", 680),
    ("Console Sony PS5 Pro 2 To", "PS5 Pro", 750),
    ("Sony PlayStation 5 Slim", "PS5", 450),
    ("Nintendo Switch 2 + Mario Kart World", "Nintendo Switch 2", 420),
    ("Console Nintendo Switch OLED blanche", "Nintendo Switch OLED", 290),
    ("Meta Quest 3 512 Go", "Meta Quest 3", 480),
    ("Apple iPhone 16 Pro Max 256 Go", "iPhone 16 Pro Max", 1300),
    ("Apple iPhone 16 Pro 128 Go", "iPhone 16 Pro", 1050),
    ("Apple iPhone 16 128 Go", "iPhone 16", 820),
    ("iPhone 16 pour 99€", "iPhone 16", 820),
    ("Apple MacBook Air 13\" M4 16 Go", "MacBook Air M3/M4", 1050),
    ("Apple AirPods Pro 2 (USB-C)", "AirPods Pro 2", 210),
    ("Dyson V15 Detect Absolute", "Dyson V15", 550),
    ("Sony WH-1000XM5 noir", "Sony WH-1000XM5", 290),
    ("LG OLED evo C5 65\" 4K", "TV OLED 65\"", 1200),
    ("Samsung OLED S90D 55 pouces", "TV OLED 55\"", 850),
    ("Sony Bravia 8 II OLED 77\"", "TV OLED 77\"", 1200),
    ("LG UltraGear OLED 27\" 240 Hz", "Écran OLED 27\"", 600),
    # PC et portables complets : base du GPU + 450 €
    ("PC de bureau Alienware Aurora R16 - i9 285K, RTX 5080", "PC fixe RTX 5080", 1600),
    ("PC de bureau Alienware Aurora R16 - i9 285K, RTX 5080, SSD 8 To, Refroidissement Liquide", "PC fixe RTX 5080", 1600),
    ("PC portable Asus TUF Gaming A16 RTX 5070", "Ordinateur portable RTX 5070", 1070),
    ("PC gamer Ryzen 7 9800X3D RTX 5070 Ti", "PC fixe RTX 5070 Ti", 1330),
    ("Alienware Aurora R16 i7 32 Go", "Alienware Aurora", 1400),
    ("Lenovo ThinkPad T16 Gen 3", "ThinkPad T16", 1000),
    ("Lenovo ThinkPad X1 Carbon Gen 13", "ThinkPad X1 Carbon", 1300),
    ("Apple Mac mini M4 16 Go", "Mac mini M4", 650),
    # Stockage par capacité
    ("SANDISK Creator Class 10 microSDXC Memory Card - 512 GB", "Stockage 512 Go", 35),
    ("SanDisk Extreme microSD 1 To + adaptateur SD", "Stockage 1 To", 65),
    ("Samsung 990 Pro SSD NVMe M.2 2 To", "Stockage 2 To", 120),
    ("Crucial T705 SSD 4TB PCIe 5.0", "Stockage 4 To", 230),
    # Divers
    ("Garmin Fenix 8 AMOLED 47 mm", "Garmin Fenix 7/8", 500),
    ("GoPro HERO13 Black", "GoPro Hero 12/13", 320),
    ("DJI Mini 4 Pro Fly More Combo", "DJI Mini 3/4 Pro", 600),
    ("DJI Osmo Pocket 3 Creator Combo", "DJI Osmo Pocket 3", 480),
    ("KitchenAid Artisan 5KSM175PS", "KitchenAid Artisan", 380),
    ("Vorwerk Thermomix TM7", "Thermomix", 1100),
    ("Philips Hue White & Color Starter Kit E27", "Philips Hue Starter Kit", 110),
    ("Valve Steam Deck OLED 1 To", "Steam Deck OLED", 520),
    ("ASUS ROG Ally X 1 To", "ROG Ally X", 750),
    ("Xbox Series X 1 To", "Xbox Series X", 450),
    ("Samsung Galaxy S25 Ultra 512 Go", "Galaxy S25 Ultra", 1000),
])
def test_catalog_matches(title, name, price):
    match = lookup(title)
    assert match is not None and match.name == name and match.price == price


def test_catalog_has_80_plus_products():
    from scraper.reference_catalog import catalog_size
    assert catalog_size() >= 80


@pytest.mark.parametrize("title", [
    "Coque pour AirPods Pro 2",
    "Verre trempé iPhone 16 Pro",
    "PC gamer Ryzen 7 9800X3D 32 Go DDR5",   # PC sans GPU identifié : pas la base d'un CPU
    "WD Elements disque dur 4 To",
    "Câble d'alimentation pour PC RTX 5080",
    "Boîtier PC gamer compatible RTX 5070",
    "Lecteur de cartes SanDisk microSD 1 To",
    "Forza Horizon 5 Xbox Series X",
    "Elden Ring PS5",
    "Mario Kart World Switch 2",
    "Manette DualSense PS5",
    "Filtre Dyson V15",
    "Support mural TV OLED 65\"",
    "Philips Hue Lightstrip für 55 Zoll TV",
    "iPhone 16 avec forfait 5G",
    "Chaise de jardin",
])
def test_catalog_refuses_non_comparable(title):
    assert lookup(title) is None


def test_catalog_currency_and_refurbished():
    assert lookup("Sony PlayStation 5 Slim", "GBP").price == round(450 * 0.87, 2)
    assert lookup("Apple iPhone 16 128 Go reconditionné").price == 574.0
    assert lookup("Sony PlayStation 5 Slim", "PLN") is None


def test_rtx_5070_at_165_is_certified_without_any_external_price():
    deal = make(title="Zotac Gaming GeForce RTX 5070 Twin Edge OC Nvidia Grafikkarte 12GB", price=165.0,
                published=(NOW - timedelta(days=3)).isoformat())
    a = analyze(deal, NOW)
    assert a.market_source == "catalog" and a.market_price == 620
    assert a.real_discount == 73
    assert a.certified and a.score >= 95
    assert "📚 Base catalogue : RTX 5070" in a.reasons


def test_external_market_price_beats_catalog():
    a = analyze(make(title="Zotac RTX 5070 Twin Edge", price=500.0, next_best_price=549.0), NOW)
    assert a.market_source == "pepper" and a.market_price == 549.0


def test_catalog_alone_does_not_clear_third_party_sellers():
    a = analyze(make(title="RTX 5070 Gainward (Vendeur PromoTech88)", merchant="Cdiscount", price=165.0), NOW)
    assert a.rejected == "Vendeur tiers non vérifié"


def test_normal_price_against_catalog_is_not_a_glitch():
    a = analyze(make(title="Sony PlayStation 5 Slim", price=429.0), NOW)
    assert not a.certified and a.level == "normal"


# --- JSON-LD ----------------------------------------------------------------------------

PRODUCT_PAGE = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"BreadcrumbList"}</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"Product","name":"GeForce RTX 5070","brand":{"@type":"Brand","name":"Zotac"},
 "gtin13":"4895173627417","offers":{"@type":"Offer","price":"165.00","priceCurrency":"EUR",
 "availability":"https://schema.org/InStock"}}]}
</script></head></html>"""

AGGREGATE_PAGE = """<script type="application/ld+json">[{"@type":["Product"],"name":"RTX 5070",
"offers":{"@type":"AggregateOffer","lowPrice":"559,90","highPrice":"689,00","priceCurrency":"EUR"}}]</script>"""


def test_parse_product_jsonld_offer():
    product = parse_product_jsonld(PRODUCT_PAGE)
    assert product["brand"] == "Zotac"
    assert product["gtin"] == "4895173627417"
    assert product["price"] == 165.0 and product["currency"] == "EUR"
    assert product["availability"] == "InStock" and not product["aggregate"]


def test_parse_product_jsonld_aggregate_offer():
    product = parse_product_jsonld(AGGREGATE_PAGE)
    assert product["aggregate"]
    assert (product["low_price"], product["high_price"]) == (559.9, 689.0)


def test_parse_product_jsonld_handles_garbage():
    assert parse_product_jsonld('<script type="application/ld+json">{oops</script>') is None
    assert parse_product_jsonld("") is None


def test_blocked_hosts_are_not_fetched():
    assert not resolvable("https://www.amazon.de/dp/B0FGYBDH38")
    assert not resolvable("https://www.idealo.de/preisvergleich/OffersOfProduct/1")
    assert resolvable("https://www.petit-marchand.fr/produit/rtx-5070")


def run(coro):
    return asyncio.run(coro)


def test_resolve_product_and_live_check():
    pages = {
        "shop.test": PRODUCT_PAGE.replace("165.00", "649.00"),       # prix déjà corrigé
        "geizhals.de": AGGREGATE_PAGE,
    }

    def handler(request):
        return httpx.Response(200, text=pages[request.url.host], headers={"content-type": "text/html"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            single = await resolve_product(client, "https://shop.test/rtx")
            deal = {"id": "d", "price": 165.0,
                    "merchant_links": ["https://shop.test/rtx", "https://geizhals.de/rtx-5070-a1.html"]}
            check, live = await PriceVerifier().verify(client, deal)
            return single, check, live

    single, check, live = run(go())
    assert single["price"] == 649.0 and single["comparator"] is False
    assert live["live_price"] == 649.0 and live["live_available"] is True
    assert live["live_corrected"] is True and live["ean"] == "4895173627417"
    assert check.median == round((689.0 + (559.9 + 689.0) / 2) / 2, 2)
    assert check.as_fields()["market_sources"] == ["jsonld"]


def test_resolve_product_survives_timeouts():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await resolve_product(client, "https://shop.test/x")

    assert run(go()) is None


# --- Liens marchands du flux --------------------------------------------------------------

def test_merchant_links_filter_pepper_and_images():
    body = ('<a href="https://www.amazon.de/dp/B0FGYBDH38?tag=x">A</a>'
            '<a href="https://www.mydealz.de/deals/x-1">m</a>'
            '<img src="x"><a href="https://boulanger.scene7.com/a.jpg">i</a>'
            '<a href="https://www.idealo.de/preisvergleich/OffersOfProduct/1">i</a>'
            '<a href="https://www.amazon.de/dp/B0FGYBDH38?tag=x">dup</a>')
    assert merchant_links(body) == ["https://www.amazon.de/dp/B0FGYBDH38?tag=x",
                                    "https://www.idealo.de/preisvergleich/OffersOfProduct/1"]


def test_parse_feed_extracts_asin_and_ean_from_description():
    feed = b"""<?xml version="1.0"?><rss xmlns:pepper="http://www.pepper.com/rss" version="2.0"><channel>
<item><pepper:merchant name="Amazon" price="165&#8364;"/><title><![CDATA[RTX 5070]]></title>
<description><![CDATA[<strong>x</strong><a href="https://www.amazon.de/dp/B0FGYBDH38">Amazon</a> EAN: 4895173627417]]></description>
<link>https://www.mydealz.de/deals/rtx-5070-42</link><pubDate>Sun, 01 Nov 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""
    (deal,) = parse_feed(feed, {"id": "mydealz", "country": "DE"})
    assert deal["asin"] == "B0FGYBDH38"
    assert deal["ean"] == "4895173627417"
    assert deal["merchant_links"] == ["https://www.amazon.de/dp/B0FGYBDH38"]
