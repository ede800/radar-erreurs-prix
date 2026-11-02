"""Retry HTTP, mémoire SQLite, vérification marché, liens panier, alertes et cycle complet.

Aucun accès réseau : toutes les requêtes passent par ``httpx.MockTransport``.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from scraper.history import History, product_key
from scraper.merchant_apis import (
    KeepaProvider,
    MarketQuote,
    amazon_links,
    checkout_links,
    extract_asin,
    search_query,
)
from scraper.net import get_with_retry
from scraper.notifier import Notifier, discord_payload, money, telegram_payload
from scraper.pipeline import Radar, Settings, merge
from scraper.price_verifier import PriceVerifier, summarize
from scraper.sources.pepper_source import PepperSource

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


async def no_sleep(_delay):
    return None


# --- Retry / backoff ------------------------------------------------------------------

def test_retry_on_429_then_success_rotates_user_agent():
    calls = []

    def handler(request):
        calls.append(request.headers["User-Agent"])
        return httpx.Response(429 if len(calls) < 3 else 200, text="ok")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await get_with_retry(client, "https://x.test/", sleep=no_sleep)

    response = run(go())
    assert response.status_code == 200
    assert len(calls) == 3
    assert all(ua.startswith("Mozilla/5.0") for ua in calls)


def test_retry_gives_up_with_status_error():
    delays = []

    async def record(delay):
        delays.append(delay)

    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(403))
        async with httpx.AsyncClient(transport=transport) as client:
            await get_with_retry(client, "https://x.test/", attempts=3, base_delay=1.0, sleep=record)

    with pytest.raises(httpx.HTTPStatusError):
        run(go())
    assert len(delays) == 2
    assert 1.0 <= delays[0] < 1.5 and 2.0 <= delays[1] < 2.5       # backoff exponentiel + jitter


def test_no_retry_on_404():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await get_with_retry(client, "https://x.test/", sleep=no_sleep)

    with pytest.raises(httpx.HTTPStatusError):
        run(go())
    assert len(calls) == 1


# --- Mémoire SQLite -------------------------------------------------------------------

def test_history_lowest_price_and_dedup():
    with History() as history:
        a = {"id": "dealabs:1", "title": "Sony WH-1000XM5 noir", "price": 250.0, "currency": "EUR"}
        b = {"id": "mydealz:2", "title": "Sony WH-1000XM5 Noir (Amazon)", "price": 230.0, "currency": "EUR"}
        history.record_price(a, NOW)
        history.record_price(a, NOW)                       # même prix : pas de doublon
        history.record_price(b, NOW)
        key = product_key(a)
        assert key == product_key(b)
        assert history.lowest_price(key, "EUR") == 230.0
        assert history.lowest_price(key, "EUR", exclude_deal="mydealz:2") == 250.0
        assert history.lowest_price(key, "GBP") is None
        assert history.conn.execute("SELECT count(*) FROM prices").fetchone()[0] == 2


def test_history_alert_dedup_same_price_but_realert_on_lower_price():
    with History() as history:
        assert not history.already_alerted("d:1", 99.0)
        history.mark_alerted("d:1", 99.0, NOW)
        assert history.already_alerted("d:1", 99.0)
        assert history.already_alerted("d:1", 120.0)
        assert not history.already_alerted("d:1", 49.0)     # nouvelle baisse → nouvelle alerte


def test_history_persists_on_disk(tmp_path):
    path = tmp_path / "data" / "history.db"
    with History(path) as history:
        history.mark_seen("d:1", NOW, kept=False)
    with History(path) as history:
        assert history.is_known("d:1")


def test_product_key_prefers_asin():
    assert product_key({"asin": "B0ABCDEFGH", "title": "x"}) == "asin:B0ABCDEFGH"


# --- Vérification marché --------------------------------------------------------------

def test_summarize_median_and_floor():
    quotes = [MarketQuote("a", 400.0), MarketQuote("b", 500.0), MarketQuote("c", 450.0, "average"),
              MarketQuote("k", 99.0, "lowest")]               # le plus bas = l'erreur elle-même
    check = summarize(quotes, price=99.0)
    assert check.median == 450.0
    assert check.floor == 400.0


def test_summarize_without_quotes():
    assert summarize([], 10.0).median is None


def test_verifier_uses_pepper_best_price_and_survives_provider_failure():
    class Broken:
        name = "broken"

        async def quotes(self, client, deal):
            raise httpx.ConnectError("boom")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
            return await PriceVerifier([Broken()]).verify(client, {"id": "d:1", "price": 50.0, "next_best_price": 449})

    check, live = run(go())
    assert live == {}
    assert check.median == 449.0
    assert check.as_fields()["market_sources"] == ["pepper"]


def test_keepa_provider_parses_cents():
    payload = {"products": [{"stats": {"avg90": [79900, 82000], "min": [[1, 5000], [1, 75000]]}}]}

    def handler(request):
        assert request.url.params["asin"] == "B0ABCDEFGH" and request.url.params["domain"] == "4"
        return httpx.Response(200, json=payload)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            deal = {"id": "d", "asin": "B0ABCDEFGH", "merchant": "Amazon.fr", "country": "FR"}
            return await KeepaProvider("k").quotes(client, deal)

    quotes = run(go())
    assert MarketQuote("keepa:avg90", 799.0, "average") in quotes
    assert MarketQuote("keepa:min", 50.0, "lowest") in quotes
    assert summarize(quotes, 50.0).median == 809.5


# --- Liens d'achat --------------------------------------------------------------------

def test_extract_asin():
    assert extract_asin("https://www.amazon.fr/dp/B0CHX1W1XY?tag=x") == "B0CHX1W1XY"
    assert extract_asin(None, "pas d'asin ici") is None


def test_amazon_cart_link():
    links = amazon_links("B0CHX1W1XY", "amazon.de")
    assert links["product_url"] == "https://www.amazon.de/dp/B0CHX1W1XY"
    assert links["cart_url"] == "https://www.amazon.de/gp/aws/cart/add.html?ASIN.1=B0CHX1W1XY&Quantity.1=1"


def test_checkout_links_cart_only_for_amazon_deals():
    deal = {"marketplace": "Amazon", "merchant": "Amazon.co.uk", "country": "UK", "asin": "B0CHX1W1XY"}
    links = checkout_links(deal)
    assert links["cart_url"].startswith("https://www.amazon.co.uk/gp/aws/cart/add.html")
    assert links["amazon_eu"]["it"] == "https://www.amazon.it/dp/B0CHX1W1XY"
    other = checkout_links({"marketplace": "Fnac", "asin": "B0CHX1W1XY"})
    assert "cart_url" not in other and other["amazon_eu"]["es"] == "https://www.amazon.es/dp/B0CHX1W1XY"


def test_amazon_eu_search_links_without_asin():
    links = checkout_links({"marketplace": "Autre", "catalog_name": "RTX 5070", "title": "x"})
    assert links["amazon_eu_kind"] == "search"
    assert set(links["amazon_eu"]) == {"fr", "de", "es", "it", "uk"}
    assert links["amazon_eu"]["de"] == "https://www.amazon.de/s?k=RTX+5070"
    assert checkout_links({"marketplace": "Fnac", "title": "Chaise"}) == {}


def test_search_query_strips_noise():
    assert search_query("[Preisfehler] Zotac RTX 5070 (MediaMarkt) für 165€") == "Zotac RTX 5070 für"


# --- Alertes --------------------------------------------------------------------------

ALERT_DEAL = {
    "id": "dealabs:9", "title": "Sony WH-1000XM5 <édition>", "link": "https://www.dealabs.com/x-9",
    "price": 120.0, "currency": "EUR", "market_price": 380.0, "real_discount": 68, "savings": 260.0,
    "resale_margin": 203.0, "score": 92, "seller": "direct", "merchant": "Amazon.fr",
    "reasons": ["✅ CERTIFIED PRICE GLITCH"], "cart_url": "https://www.amazon.fr/gp/aws/cart/add.html?ASIN.1=B0X",
    "certified": True,
}


def test_money_format():
    assert money(1299.5, "EUR") == "1 299,50 €"
    assert money(0, "GBP") == "Gratuit"


def test_payloads_contain_metrics_and_cart_link():
    discord = discord_payload(ALERT_DEAL)["embeds"][0]["description"]
    assert "GLITCH SCORE : 92/100" in discord
    assert "prix marché 380,00 €" in discord
    assert "Économie : 260,00 €" in discord
    assert "Officiel" in discord
    assert "Ajout direct au panier" in discord
    telegram = telegram_payload(ALERT_DEAL, "42")
    assert telegram["chat_id"] == "42" and telegram["parse_mode"] == "HTML"
    assert "&lt;édition&gt;" in telegram["text"]


def test_notifier_sends_once_per_price():
    posts = []

    def handler(request):
        posts.append(request.url.host)
        return httpx.Response(200, json={"ok": True})

    env = {"DISCORD_WEBHOOK_URL": "https://discord.test/api/webhooks/1/abc",
           "TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42"}
    deals = [dict(ALERT_DEAL), dict(ALERT_DEAL, id="dealabs:10", score=80)]   # 80 : pas > 80

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
          with History() as history:
            notifier = Notifier(env)
            first = await notifier.notify(client, deals, history, NOW)
            second = await notifier.notify(client, deals, history, NOW)
            return first, second

    first, second = run(go())
    assert [d["id"] for d in first] == ["dealabs:9"]
    assert second == []
    assert sorted(posts) == ["api.telegram.org", "discord.test"]


def test_notifier_disabled_without_env():
    assert not Notifier({}).enabled
    assert not Notifier({"TELEGRAM_BOT_TOKEN": "x"}).enabled        # chat id manquant


def test_notifier_failure_does_not_mark_alerted():
    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
          with History() as history:
            sent = await Notifier({"DISCORD_WEBHOOK_URL": "https://d.test/w"}).notify(
                client, [dict(ALERT_DEAL)], history, NOW)
            return sent, history.already_alerted("dealabs:9", 120.0)

    assert run(go()) == ([], False)


# --- Fusion et cycle complet ------------------------------------------------------------

def test_merge_keeps_enrichment_and_first_seen():
    old = [{"id": "a", "first_seen": "2026-11-01T00:00:00+00:00", "published": "2026-11-01T00:00:00+00:00",
            "temperature": 300, "next_best_price": 500, "stream": "glitch", "alerted": "x"}]
    fresh = [{"id": "a", "published": "2026-11-01T00:00:00+00:00", "stream": "fresh"}]
    (deal,) = merge(old, fresh, NOW)
    assert deal["first_seen"] == "2026-11-01T00:00:00+00:00"
    assert deal["temperature"] == 300 and deal["next_best_price"] == 500
    assert deal["stream"] == "glitch" and deal["alerted"] == "x"


def test_merge_drops_old_deals():
    old = [{"id": "a", "first_seen": (NOW - timedelta(days=90)).isoformat()}]
    assert merge(old, [], NOW) == []


def _item(thread_id, title, merchant, price, minutes_ago):
    published = (NOW - timedelta(minutes=minutes_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return (f'<item><pepper:merchant name="{merchant}" price="{price}&#8364;"/>'
            f"<title><![CDATA[{title}]]></title><description><![CDATA[<strong>x</strong>desc]]></description>"
            f"<link>https://www.dealabs.com/bons-plans/p-{thread_id}</link><pubDate>{published}</pubDate></item>")


def _rss(*items):
    return ('<?xml version="1.0"?><rss xmlns:pepper="http://www.pepper.com/rss" '
            'xmlns:media="http://search.yahoo.com/mrss/" version="2.0"><channel>'
            + "".join(items) + "</channel></rss>").encode()


def test_decode_redirect_and_merchant_info():
    from scraper.price_verifier import decode_redirect, merchant_info, visit_url
    location = ("https://path.dealabs.com/pepper-fr/redirect?url=https%3A%2F%2Fwww.amazon.fr%2Fdp%2FB0F8R66NMJ"
                "%3Fsmid%3DA1X6FK5RDHNB96&product=ppr-fr-1&tag=eugc")
    target = decode_redirect(location)
    assert target == "https://www.amazon.fr/dp/B0F8R66NMJ?smid=A1X6FK5RDHNB96"
    info = merchant_info(target)
    assert info["asin"] == "B0F8R66NMJ" and info["amazon_seller"] == "amazon"
    assert "amazon_seller" not in merchant_info("https://www.amazon.de/dp/B0F8R66NMJ?smid=AXXXXXXXXXXXX")
    nested = "https://www.awin1.com/cread.php?awinmid=1&ued=https%3A%2F%2Fwww.fnac.com%2Fa1%2Fw-4"
    assert decode_redirect(f"https://path.dealabs.com/r?url={__import__('urllib.parse').parse.quote(nested)}") \
        == "https://www.fnac.com/a1/w-4"
    assert visit_url({"id": "mydealz:42", "link": "https://www.mydealz.de/deals/x-42"}) \
        == "https://www.mydealz.de/visit/threadmain/42"


def test_resolve_redirect_never_contacts_tracker_or_merchant():
    from scraper.price_verifier import resolve_redirect
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        assert request.url.path == "/visit/threadmain/7"
        return httpx.Response(302, headers={"location": "https://path.dealabs.com/r?url=https%3A%2F%2Fshop.test%2Fp"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            ok = await resolve_redirect(client, "https://www.dealabs.com/visit/threadmain/7")
            refused = await resolve_redirect(client, "https://tracker.test/click?id=1")
            return ok, refused

    assert run(go()) == ("https://shop.test/p", None)
    assert hosts == ["www.dealabs.com"]


def test_full_cycle(tmp_path):
    site = {"id": "dealabs", "name": "Dealabs", "country": "FR", "base": "https://www.dealabs.com",
            "glitch": "/rss/groupe/erreur-de-prix", "fresh": "/rss/tous"}
    glitch_feed = _rss(_item(1, "Casque Sony WH-1000XM5", "Amazon", "119", 1))
    fresh_feed = _rss(
        _item(1, "Casque Sony WH-1000XM5", "Amazon", "119", 1),
        _item(2, "Chaussettes rigolotes", "Petit Shop", "1", 1),        # écartée d'emblée
        _item(3, "Asus ROG Ally X", "Boulanger", "649", 3),              # prix normal : écartée
    )
    pages = {
        "/bons-plans/p-1": ',"nextBestPrice":379,"temperature":250,"temperatureLevel":"Hot1",'
                           '"threadId":"1","issueReported":false,"isExpired":false',
        "/bons-plans/p-3": ',"nextBestPrice":699,"temperature":20,"threadId":"3","issueReported":false',
    }
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == site["glitch"]:
            return httpx.Response(200, content=glitch_feed)
        if request.url.path == site["fresh"]:
            return httpx.Response(200, content=fresh_feed)
        if request.url.path in pages:
            return httpx.Response(200, text=pages[request.url.path])
        if request.url.host == "discord.test":
            return httpx.Response(204)
        if request.url.path == "/visit/threadmain/1":
            return httpx.Response(302, headers={"location": "https://path.dealabs.com/pepper-fr/redirect?url="
                                                "https%3A%2F%2Fwww.amazon.fr%2Fdp%2FB0F8R66NMJ%3Fsmid%3DA1X6FK5RDHNB96"})
        return httpx.Response(404)

    settings = Settings(data_file=tmp_path / "deals.json", db_path=tmp_path / "history.db")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with History(settings.db_path) as history:
                radar = Radar(client, [PepperSource(site)], history,
                              notifier=Notifier({"DISCORD_WEBHOOK_URL": "https://discord.test/w"}),
                              settings=settings)
                first = await radar.cycle(NOW)
                second = await radar.cycle(NOW + timedelta(seconds=20))
                return first, second

    first, second = run(go())
    data = json.loads(settings.data_file.read_text(encoding="utf-8"))
    assert data["version"] == 2
    ids = [d["id"] for d in data["deals"]]
    assert ids == ["dealabs:1"]
    deal = data["deals"][0]
    assert deal["market_median"] == 379 and deal["real_discount"] == 69
    assert deal["certified"] and deal["level"] == "critical"
    assert deal["alerted"]
    assert "/bons-plans/p-2" not in requests                           # pas de marque → pas de page
    assert [d["id"] for d in first["sent"]] == ["dealabs:1"]
    assert second["sent"] == []                                         # jamais deux fois
    assert requests.count("/bons-plans/p-1") == 1                       # enrichissement mis en cache
    assert requests.count("/visit/threadmain/1") == 1                   # redirection résolue une seule fois
    assert deal["asin"] == "B0F8R66NMJ" and deal["amazon_seller"] == "amazon"
    assert deal["seller"] == "direct"
    assert deal["cart_url"].startswith("https://www.amazon.fr/gp/aws/cart/add.html?ASIN.1=B0F8R66NMJ")
    assert "/dp/B0F8R66NMJ" not in requests and "/pepper-fr/redirect" not in requests
