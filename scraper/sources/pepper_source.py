"""Ingestion du réseau Pepper (Dealabs, mydealz, Preisjäger, HotUKDeals).

Deux types de flux par site :
  - ``glitch`` : le groupe « erreur de prix / Preisfehler / price glitch » (déjà trié par la communauté) ;
  - ``fresh``  : le flux de *toutes* les nouvelles annonces, publié quelques secondes après la
    soumission. Seules les annonces présentant un indicateur d'erreur de prix sont conservées.

La page d'une annonce embarque un JSON avec la température (votes), le « meilleur prix
ailleurs » (``nextBestPrice``) et l'état expiré : ``parse_thread_page`` les extrait.
"""

from __future__ import annotations

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime

import httpx

from ..merchant_apis import extract_asin, extract_ean
from ..net import get_with_retry

SITES = [
    {"id": "dealabs", "name": "Dealabs", "country": "FR", "base": "https://www.dealabs.com",
     "glitch": "/rss/groupe/erreur-de-prix", "fresh": "/rss/tous"},
    {"id": "mydealz", "name": "mydealz", "country": "DE", "base": "https://www.mydealz.de",
     "glitch": "/rss/gruppe/preisfehler", "fresh": "/rss/new"},
    {"id": "preisjaeger", "name": "Preisjäger", "country": "AT", "base": "https://www.preisjaeger.at",
     "glitch": "/rss/gruppe/preisfehler", "fresh": "/rss/alle"},
    {"id": "hotukdeals", "name": "HotUKDeals", "country": "UK", "base": "https://www.hotukdeals.com",
     "glitch": "/rss/tag/price-glitch", "fresh": "/rss/new"},
]

# Ordre important : le premier motif trouvé dans le nom du marchand l'emporte.
MARKETPLACES = [
    ("amazon", "Amazon"), ("cdiscount", "Cdiscount"), ("fnac", "Fnac"), ("darty", "Darty"),
    ("ebay", "eBay"), ("aliexpress", "AliExpress"), ("rakuten", "Rakuten"),
    ("boulanger", "Boulanger"), ("leclerc", "Leclerc"), ("carrefour", "Carrefour"),
    ("auchan", "Auchan"), ("media markt", "MediaMarkt"), ("mediamarkt", "MediaMarkt"),
    ("saturn", "Saturn"), ("otto", "Otto"), ("kaufland", "Kaufland"), ("argos", "Argos"),
    ("currys", "Currys"), ("walmart", "Walmart"),
]

GLITCH_KEYWORDS_RE = re.compile(
    r"erreur de prix|bug de prix|prix fou|prix bug|erreur d'?[ée]tiquetage|"
    r"preisfehler|fehlerpreis|preis-?bug|"
    r"price (?:error|glitch|mistake)|pricing (?:error|glitch|mistake)|\bglitch\b|\berror\b",
    re.IGNORECASE,
)

ORIGINAL_PRICE_RE = re.compile(
    r"(?:au lieu de|prix (?:habituel|normal|de base)|statt|uvp|was|rrp|normally)"
    r"\s*:?\s*(?:de\s*)?([€£$]?\s?\d[\d.,\s]*\s?(?:€|£|\$|eur)?)",
    re.IGNORECASE,
)
NS = {"pepper": "http://www.pepper.com/rss", "media": "http://search.yahoo.com/mrss/"}

HREF_RE = re.compile(r'href="(https?://[^"]+)"', re.IGNORECASE)
_NOT_MERCHANT_RE = re.compile(
    r"^https?://(?:[^/]*\.)?(?:dealabs\.com|mydealz\.de|preisjaeger\.at|hotukdeals\.com|pepper\.com|"
    r"scene7\.com|static-pepper|youtube\.com|youtu\.be|twitter\.com|x\.com|facebook\.com|instagram\.com|"
    r"tiktok\.com|reddit\.com|t\.me|discord\.gg)|\.(?:jpe?g|png|gif|webp|pdf)(?:\?|$)",
    re.IGNORECASE,
)


# --- Parsing ---------------------------------------------------------------------

def parse_price(text: str | None) -> tuple[float | None, str | None]:
    """'1.299,99€' -> (1299.99, 'EUR'), '£1.99' -> (1.99, 'GBP')."""
    if not text:
        return None, None
    lowered = text.lower()
    if "gratuit" in lowered or "kostenlos" in lowered or "free" in lowered:
        return 0.0, None
    currency = None
    if "€" in text or "eur" in lowered:
        currency = "EUR"
    elif "£" in text:
        currency = "GBP"
    elif "$" in text:
        currency = "USD"
    elif "zł" in lowered or "pln" in lowered:
        currency = "PLN"
    match = re.search(r"\d[\d.,\s]*", text)
    if not match:
        return None, currency
    number = re.sub(r"\s", "", match.group(0)).rstrip(".,")
    if "," in number and "." in number:
        decimal = "," if number.rfind(",") > number.rfind(".") else "."
    elif "," in number:
        decimal = "," if re.search(r",\d{1,2}$", number) else None
    elif "." in number:
        # « 1.299 € » est un séparateur de milliers, « 1.99 » une décimale.
        decimal = None if re.search(r"\.\d{3}$", number) and currency != "USD" else "."
    else:
        decimal = None
    thousands = {",": ".", ".": ",", None: ".,"}[decimal]
    for sep in thousands:
        number = number.replace(sep, "")
    if decimal:
        number = number.replace(decimal, ".")
    try:
        return float(number), currency
    except ValueError:
        return None, currency


def classify_marketplace(merchant: str | None, title: str) -> str:
    haystack = (merchant or title).lower()
    for needle, label in MARKETPLACES:
        if needle in haystack:
            return label
    return "Autre"


def clean_text(raw: str | None, limit: int = 280) -> str:
    if not raw:
        return ""
    text = re.sub(r"<br\s*/?>|</p>", " ", raw, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def find_original_price(description: str, price: float | None) -> float | None:
    if price is None:
        return None
    for match in ORIGINAL_PRICE_RE.finditer(description):
        value, _ = parse_price(match.group(1))
        if value and value > price:
            return value
    return None


def merchant_links(html_body: str, limit: int = 6) -> list[str]:
    """Liens directs vers des marchands / comparateurs postés dans la description."""
    links: list[str] = []
    for url in HREF_RE.findall(html_body or ""):
        url = html.unescape(url)
        if not _NOT_MERCHANT_RE.search(url) and url not in links:
            links.append(url)
    return links[:limit]


def has_glitch_keyword(text: str) -> bool:
    return bool(GLITCH_KEYWORDS_RE.search(text))


def parse_feed(xml_bytes: bytes, source: dict, stream: str = "glitch") -> list[dict]:
    root = ET.fromstring(xml_bytes)
    deals = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        merchant_el = item.find("pepper:merchant", NS)
        merchant = merchant_el.get("name") if merchant_el is not None else None
        price_text = merchant_el.get("price") if merchant_el is not None else None
        price, currency = parse_price(price_text)
        raw_description = item.findtext("description") or ""
        # La description commence par « <strong>prix - marchand</strong> » : on l'écarte.
        body = re.sub(r"^<strong>.*?</strong>", "", raw_description, count=1, flags=re.S)
        full_text = clean_text(body, limit=10_000)
        image_el = item.find("media:content", NS)
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            published_iso = published.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            published_iso = None
        original = find_original_price(full_text, price)
        links = merchant_links(body)
        deals.append({
            "id": f"{source['id']}:{(item.findtext('guid') or link).rstrip('/').rsplit('-', 1)[-1]}",
            "title": title,
            "link": link,
            "merchant": merchant,
            "marketplace": classify_marketplace(merchant, title),
            "price": price,
            "original_price": original,
            "discount": round(100 * (1 - price / original)) if original else None,
            "currency": currency or ("GBP" if source["country"] == "UK" else "EUR"),
            "category": (item.findtext("category") or "").strip() or None,
            "image": image_el.get("url") if image_el is not None else None,
            "description": clean_text(body),
            "source": source["id"],
            "country": source["country"],
            "published": published_iso,
            "stream": stream,
            "glitch_keyword": stream == "glitch" or has_glitch_keyword(f"{title} {full_text}"),
            "merchant_links": links,
            "asin": extract_asin(*links, title, full_text),
            "ean": extract_ean(title, full_text),
        })
    return deals


def _last_number(chunk: str, key: str) -> float | None:
    matches = re.findall(rf'"{key}":(-?\d+(?:\.\d+)?|null)', chunk)
    if not matches or matches[-1] == "null":
        return None
    return float(matches[-1])


def parse_thread_page(page: str, thread_id: str) -> dict:
    """Extrait température, meilleur prix ailleurs et état expiré du JSON de la page."""
    anchor = re.search(rf'"threadId":"{re.escape(thread_id)}","issueReported"', page)
    if not anchor:
        return {}
    start = anchor.start()
    before = page[max(0, start - 400):start]   # ses champs précèdent son threadId de ~50-300 car.
    other = before.rfind('"threadId"')        # ne pas déborder sur une annonce voisine
    if other != -1:
        before = before[other:]
    after = page[start:start + 8000]
    expired = re.search(r'"isExpired":(true|false)', after)
    info = {
        "temperature": _last_number(before, "temperature"),
        "next_best_price": _last_number(before, "nextBestPrice") or None,
        "expired": expired.group(1) == "true" if expired else None,
    }
    host = re.findall(r'"linkHost":"([^"]*)"', before)
    if host:
        info["link_host"] = host[-1]
    return {k: v for k, v in info.items() if v is not None}


# --- Récupération ------------------------------------------------------------------

class PepperSource:
    """Lit les flux d'un site Pepper en requêtes conditionnelles (ETag / Last-Modified)."""

    def __init__(self, site: dict, streams: tuple[str, ...] = ("glitch", "fresh")):
        self.site = site
        self.streams = streams
        self.validators: dict[str, dict[str, str]] = {}
        self.cache: dict[str, list[dict]] = {}

    @property
    def id(self) -> str:
        return self.site["id"]

    async def _fetch_stream(self, client: httpx.AsyncClient, stream: str) -> list[dict]:
        url = self.site["base"] + self.site[stream]
        response = await get_with_retry(client, url, headers=self.validators.get(url, {}))
        if response.status_code == 304:
            return self.cache.get(url, [])
        validators = {}
        if etag := response.headers.get("ETag"):
            validators["If-None-Match"] = etag
        if modified := response.headers.get("Last-Modified"):
            validators["If-Modified-Since"] = modified
        self.validators[url] = validators
        deals = parse_feed(response.content, self.site, stream)
        self.cache[url] = deals
        return deals

    async def fetch(self, client: httpx.AsyncClient) -> list[dict]:
        results = await asyncio.gather(*(self._fetch_stream(client, s) for s in self.streams),
                                       return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if len(errors) == len(results):
            raise errors[0]
        by_id: dict[str, dict] = {}
        for result in results:
            if isinstance(result, BaseException):
                continue
            for deal in result:
                known = by_id.get(deal["id"])
                if known is None or deal["stream"] == "glitch":
                    if known:
                        deal["glitch_keyword"] = True
                    by_id[deal["id"]] = deal
        return list(by_id.values())

    async def enrich(self, client: httpx.AsyncClient, deal: dict) -> dict:
        thread_id = deal["id"].split(":", 1)[1]
        response = await get_with_retry(client, deal["link"], attempts=2)
        return parse_thread_page(response.text, thread_id)


def default_sources() -> list[PepperSource]:
    return [PepperSource(site) for site in SITES]
