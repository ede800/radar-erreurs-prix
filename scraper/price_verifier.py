"""Vérification croisée du prix : estime le **prix marché réel** d'un produit.

Le prix barré du marchand n'est jamais utilisé ici. Sources, par ordre de confiance :
  1. le « meilleur prix ailleurs » saisi par la communauté Pepper (``nextBestPrice``) ;
  2. les API structurées de ``merchant_apis`` (Keepa si ``KEEPA_API_KEY``, optionnel) ;
  3. le JSON-LD schema.org des liens postés dans l'annonce : une page de **comparateur**
     (``AggregateOffer``) donne une fourchette de prix marché ;
  4. à défaut, le catalogue de prix de référence (``reference_catalog``), appliqué par
     ``glitch_engine``.

Le JSON-LD de la **fiche marchand** donne le prix *actuel* de l'offre — c'est-à-dire le prix
de l'erreur elle-même, pas le prix marché. Il sert donc à vérifier que l'erreur est encore
en ligne (prix, disponibilité) et à confirmer la marque, jamais de référence de remise.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .merchant_apis import MarketQuote
from .net import browser_headers

JSONLD_TIMEOUT = 3.0
JSONLD_MAX_BYTES = 1_500_000
# Sites qui bloquent systématiquement les robots (captcha / 403) : inutile d'essayer.
BLOCKED_HOSTS = ("amazon.", "idealo.", "ebay.", "cdiscount.", "fnac.", "darty.", "boulanger.",
                 "mediamarkt.", "saturn.", "otto.de", "aliexpress.", "temu.", "keepa.com", "google.")
# Comparateurs : leur AggregateOffer reflète le marché.
COMPARATORS = ("geizhals.", "ledenicheur.", "pricespy.", "prisjakt.", "pricerunner.", "kelkoo.",
               "billiger.de", "guenstiger.de", "toppreise.ch", "skinflint.")
LD_JSON_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


@dataclass
class MarketCheck:
    median: float | None
    floor: float | None
    quotes: list[MarketQuote] = field(default_factory=list)

    def as_fields(self) -> dict:
        return {
            "market_median": self.median,
            "market_floor": self.floor,
            "market_sources": sorted({q.source.split(":")[0] for q in self.quotes}),
        }


def summarize(quotes: list[MarketQuote], price: float | None) -> MarketCheck:
    reference = [q.price for q in quotes if q.kind != "lowest" and q.price > 0]
    if not reference:
        return MarketCheck(None, None, quotes)
    median = round(statistics.median(reference), 2)
    # Un « plus bas 90 jours » égal au prix de l'erreur est l'erreur elle-même : on l'ignore.
    above = [q.price for q in quotes if price is None or q.price > price * 1.02]
    floor = round(min(above), 2) if above else median
    return MarketCheck(median, min(floor, median), quotes)


# --- JSON-LD schema.org -------------------------------------------------------------------

def _to_float(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(re.sub(r"[^\d.]", "", text) or "x")
    except ValueError:
        return None


def _iter_nodes(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_nodes(item)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "mainEntity", "itemListElement", "item"):
            if key in data:
                yield from _iter_nodes(data[key])


def _is_type(node: dict, name: str) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k.split("/")[-1] == name for k in kinds)


def parse_product_jsonld(page: str) -> dict | None:
    """Premier ``Product`` du JSON-LD : nom, marque, GTIN, prix, fourchette, devise, disponibilité."""
    for block in LD_JSON_RE.findall(page or ""):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for node in _iter_nodes(data):
            if not _is_type(node, "Product"):
                continue
            brand = node.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            elif isinstance(brand, list):
                brand = next((b.get("name") if isinstance(b, dict) else b for b in brand), None)
            offers = node.get("offers") or {}
            offers = offers if isinstance(offers, list) else [offers]
            prices, low, high, currency, availability, aggregate = [], None, None, None, None, False
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                if _is_type(offer, "AggregateOffer"):
                    aggregate = True
                    low = _to_float(offer.get("lowPrice"))
                    high = _to_float(offer.get("highPrice"))
                spec = offer.get("priceSpecification")
                if isinstance(spec, list):
                    spec = spec[0] if spec else None
                if not isinstance(spec, dict):
                    spec = {}
                price = _to_float(offer.get("price"))
                if price is None:
                    price = _to_float(spec.get("price"))
                if price is not None:
                    prices.append(price)
                currency = currency or offer.get("priceCurrency") or spec.get("priceCurrency")
                if offer.get("availability"):
                    availability = str(offer["availability"]).rsplit("/", 1)[-1]
            return {
                "name": node.get("name"),
                "brand": brand if isinstance(brand, str) else None,
                "gtin": node.get("gtin13") or node.get("gtin") or node.get("ean"),
                "sku": node.get("sku"),
                "price": min(prices) if prices else low,
                "low_price": low,
                "high_price": high,
                "currency": currency,
                "availability": availability,
                "aggregate": aggregate,
            }
    return None


def resolvable(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and not any(blocked in host for blocked in BLOCKED_HOSTS)


def is_comparator(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(c in host for c in COMPARATORS)


async def resolve_product(client: httpx.AsyncClient, url: str, timeout: float = JSONLD_TIMEOUT) -> dict | None:
    """GET rapide (3 s, une seule tentative) puis extraction du ``Product`` JSON-LD."""
    if not resolvable(url):
        return None
    try:
        response = await client.get(url, headers=browser_headers(), timeout=timeout)
    except httpx.HTTPError:
        return None
    if response.status_code != 200 or "html" not in response.headers.get("content-type", "html"):
        return None
    product = parse_product_jsonld(response.text[:JSONLD_MAX_BYTES])
    if product:
        product["url"] = str(response.url)
        product["comparator"] = is_comparator(str(response.url))
    return product


class PriceVerifier:
    def __init__(self, providers: list | None = None, resolve_links: bool = True, max_links: int = 2):
        self.providers = providers or []
        self.resolve_links = resolve_links
        self.max_links = max_links

    async def _resolve_links(self, client: httpx.AsyncClient, deal: dict) -> tuple[list[MarketQuote], dict]:
        quotes: list[MarketQuote] = []
        live: dict = {}
        price = deal.get("price")
        for url in [u for u in deal.get("merchant_links") or [] if resolvable(u)][: self.max_links]:
            product = await resolve_product(client, url)
            if not product:
                continue
            if product["comparator"]:
                for value, kind in ((product["low_price"], "lowest"), (product["high_price"], "current")):
                    if value:
                        quotes.append(MarketQuote("jsonld:comparator", value, kind))
                if product["low_price"] and product["high_price"]:
                    mid = round((product["low_price"] + product["high_price"]) / 2, 2)
                    quotes.append(MarketQuote("jsonld:comparator", mid, "average"))
            elif not live:
                live = {
                    "live_price": product["price"],
                    "live_available": None if not product["availability"]
                    else product["availability"] in ("InStock", "LimitedAvailability", "OnlineOnly", "PreOrder"),
                    "live_brand": product["brand"],
                    "live_url": product["url"],
                }
                if product.get("gtin") and not deal.get("ean"):
                    live["ean"] = str(product["gtin"])
                if price and product["price"] and product["price"] > price * 1.2:
                    live["live_corrected"] = True       # le marchand affiche déjà bien plus cher
        return quotes, live

    async def verify(self, client: httpx.AsyncClient, deal: dict,
                     resolve_links: bool | None = None) -> tuple[MarketCheck, dict]:
        """Renvoie le prix marché et, si une fiche marchand a été lue, l'état « live » de l'offre."""
        quotes: list[MarketQuote] = []
        if deal.get("next_best_price"):
            quotes.append(MarketQuote("pepper:best_elsewhere", float(deal["next_best_price"])))
        for provider in self.providers:
            try:
                quotes.extend(await provider.quotes(client, deal))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                print(f"[marché] {provider.name} en échec pour {deal['id']}: {type(exc).__name__}", file=sys.stderr)
        live: dict = {}
        if (self.resolve_links if resolve_links is None else resolve_links) and deal.get("merchant_links"):
            link_quotes, live = await self._resolve_links(client, deal)
            quotes.extend(link_quotes)
        return summarize(quotes, deal.get("price")), live
