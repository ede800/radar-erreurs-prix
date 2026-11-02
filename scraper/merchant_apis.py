"""API marchandes structurées (JSON) et liens d'achat directs.

On interroge uniquement des API officielles qui renvoient du JSON, jamais les pages HTML
des marketplaces (contraire à leurs CGU et bloqué très vite depuis un datacenter).

Fournisseur disponible :
  - **Keepa** (historique de prix Amazon, payant) : activé si ``KEEPA_API_KEY`` est défini.
    Renvoie le prix actuel, la moyenne 90 jours et le plus bas 90 jours d'un ASIN.

Pour ajouter un fournisseur (API Idealo partenaire, API d'un marchand…), implémenter
une classe avec ``name`` et ``async quotes(client, deal) -> list[MarketQuote]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urlencode

import httpx

from .net import get_with_retry

ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/gp/aw/d/|asin[=:]\s*|\b)(B0[A-Z0-9]{8})\b")
EAN_RE = re.compile(r"\b(?:EAN|GTIN)\s*:?\s*(\d{13})\b", re.IGNORECASE)

AMAZON_DOMAINS = {"FR": "amazon.fr", "DE": "amazon.de", "AT": "amazon.de", "UK": "amazon.co.uk"}
# Comparaison européenne en un clic.
AMAZON_EU = {"fr": "amazon.fr", "de": "amazon.de", "es": "amazon.es", "it": "amazon.it", "uk": "amazon.co.uk"}
KEEPA_DOMAINS = {"amazon.co.uk": 2, "amazon.de": 3, "amazon.fr": 4, "amazon.it": 8, "amazon.es": 9}


@dataclass(frozen=True)
class MarketQuote:
    source: str
    price: float
    kind: str = "current"       # current | average | lowest


def extract_asin(*texts: str | None) -> str | None:
    for text in texts:
        if text and (match := ASIN_RE.search(text)):
            return match.group(1)
    return None


def extract_ean(*texts: str | None) -> str | None:
    for text in texts:
        if text and (match := EAN_RE.search(text)):
            return match.group(1)
    return None


def amazon_domain(deal: dict) -> str:
    merchant = (deal.get("merchant") or "").lower()
    for domain in ("amazon.co.uk", "amazon.fr", "amazon.de", "amazon.it", "amazon.es"):
        if domain in merchant:
            return domain
    return AMAZON_DOMAINS.get(deal.get("country") or "", "amazon.fr")


def amazon_links(asin: str, domain: str) -> dict[str, str]:
    """Lien produit + lien « ajout direct au panier » (formulaire officiel /gp/aws/cart/add)."""
    query = urlencode({"ASIN.1": asin, "Quantity.1": 1})
    return {
        "product_url": f"https://www.{domain}/dp/{asin}",
        "cart_url": f"https://www.{domain}/gp/aws/cart/add.html?{query}",
    }


def amazon_eu_links(asin: str | None, query: str | None = None) -> dict[str, str]:
    """Fiche produit (ASIN) ou, à défaut, recherche par nom sur chaque Amazon européen."""
    if asin:
        return {cc: f"https://www.{domain}/dp/{asin}" for cc, domain in AMAZON_EU.items()}
    if query:
        return {cc: f"https://www.{domain}/s?k={quote_plus(query)}" for cc, domain in AMAZON_EU.items()}
    return {}


def search_query(title: str) -> str:
    """Titre allégé pour une recherche : sans crochets, parenthèses ni prix."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|[€£$]\s?\d[\d.,]*|\d[\d.,]*\s?[€£$]", " ", title or "")
    text = re.sub(r"(?i)\b(?:preisfehler|erreur de prix|bug de prix|price (?:error|glitch))\b[:!?-]*", " ", text)
    return " ".join(text.split()[:8])


def checkout_links(deal: dict) -> dict:
    """ASIN, lien panier direct (annonces Amazon) et liens de comparaison Amazon UE."""
    asin = deal.get("asin") or extract_asin(deal.get("link"), deal.get("title"), deal.get("description"))
    links: dict = {}
    if asin:
        links["asin"] = asin
        if deal.get("marketplace") == "Amazon":
            links.update(amazon_links(asin, amazon_domain(deal)))
        links["amazon_eu"] = amazon_eu_links(asin)
        links["amazon_eu_kind"] = "product"
    elif deal.get("marketplace") == "Amazon" or deal.get("catalog_name"):
        links["amazon_eu"] = amazon_eu_links(None, deal.get("catalog_name") or search_query(deal.get("title", "")))
        links["amazon_eu_kind"] = "search"
    return links


class KeepaProvider:
    """https://keepa.com/#!discuss/t/product-object/116 — prix en centimes, -1 = absent."""

    name = "keepa"
    URL = "https://api.keepa.com/product"
    PRICE_TYPES = (0, 1)        # 0 = vendu par Amazon, 1 = neuf (tous vendeurs)

    def __init__(self, api_key: str):
        self.api_key = api_key

    @staticmethod
    def _cents(value) -> float | None:
        if isinstance(value, list):         # « min » : [horodatage, prix]
            value = value[-1] if value else None
        return value / 100 if isinstance(value, (int, float)) and value > 0 else None

    async def quotes(self, client: httpx.AsyncClient, deal: dict) -> list[MarketQuote]:
        asin = deal.get("asin")
        domain = KEEPA_DOMAINS.get(amazon_domain(deal))
        if not asin or not domain:
            return []
        response = await get_with_retry(
            client, self.URL, attempts=2,
            params={"key": self.api_key, "domain": domain, "asin": asin, "stats": 90},
        )
        products = response.json().get("products") or []
        if not products:
            return []
        stats = products[0].get("stats") or {}
        quotes = []
        for index in self.PRICE_TYPES:
            for field, kind in (("avg90", "average"), ("min", "lowest")):
                values = stats.get(field) or []
                if index < len(values) and (price := self._cents(values[index])):
                    quotes.append(MarketQuote(f"keepa:{field}", price, kind))
        return quotes


def providers_from_env(env) -> list:
    providers = []
    if key := (env.get("KEEPA_API_KEY") or "").strip():
        providers.append(KeepaProvider(key))
    return providers
