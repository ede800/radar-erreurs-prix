"""Marchands français et européens prioritaires.

Pepper ne fournit que le nom saisi par le posteur (« Amazon », « Fnac », « Media Markt
Tarifwelt »…) et le domaine. Une fois l'URL marchande résolue (``price_verifier.resolve_redirect``,
une seule requête vers Pepper, sans suivre la redirection), on identifie ici le vrai marchand :
nom canonique, pays, type (enseigne / marketplace) et s'il fait partie des marchands
prioritaires (France et UE livrant en France).

Les sites des enseignes ne sont pas interrogés directement : Fnac, Darty, Cdiscount, Amazon,
idealo… sont protégés par captcha / anti-bot (vérifié : 403 ou page de challenge), et leurs
sitemaps publics ne contiennent pas de prix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Merchant:
    name: str
    country: str          # pays du site (FR, DE, ES, IT, UK, EU)
    kind: str             # retailer | marketplace
    hosts: tuple[str, ...]
    aliases: str          # motif sur le nom saisi dans Pepper
    priority: bool = True


MERCHANTS: list[Merchant] = [
    Merchant("Amazon.fr", "FR", "marketplace", ("amazon.fr",), r"amazon(?:\.fr)?\b(?!\.(?:de|es|it|co\.uk))"),
    Merchant("Amazon.de", "DE", "marketplace", ("amazon.de",), r"amazon\.de"),
    Merchant("Amazon.es", "ES", "marketplace", ("amazon.es",), r"amazon\.es"),
    Merchant("Amazon.it", "IT", "marketplace", ("amazon.it",), r"amazon\.it"),
    Merchant("Amazon.co.uk", "UK", "marketplace", ("amazon.co.uk",), r"amazon\.co\.uk"),
    Merchant("Fnac", "FR", "retailer", ("fnac.com",), r"\bfnac\b"),
    Merchant("Darty", "FR", "retailer", ("darty.com",), r"\bdarty\b"),
    Merchant("Boulanger", "FR", "retailer", ("boulanger.com",), r"\bboulanger\b"),
    Merchant("LDLC", "FR", "retailer", ("ldlc.com",), r"\bldlc\b"),
    Merchant("Materiel.net", "FR", "retailer", ("materiel.net",), r"mat[ée]riel\.net"),
    Merchant("Cdiscount", "FR", "marketplace", ("cdiscount.com",), r"\bcdiscount\b"),
    Merchant("Rue du Commerce", "FR", "marketplace", ("rueducommerce.fr",), r"rue ?du ?commerce"),
    Merchant("Rakuten", "FR", "marketplace", ("fr.shopping.rakuten.com", "rakuten.fr"), r"\brakuten\b"),
    Merchant("E.Leclerc", "FR", "retailer", ("e.leclerc",), r"\b(?:e\.)?leclerc\b"),
    Merchant("Carrefour", "FR", "retailer", ("carrefour.fr",), r"\bcarrefour\b"),
    Merchant("Alternate", "DE", "retailer", ("alternate.de", "alternate.fr"), r"\balternate\b"),
    Merchant("Auchan", "FR", "retailer", ("auchan.fr",), r"\bauchan\b"),
    Merchant("Back Market", "FR", "marketplace", ("backmarket.fr",), r"back ?market"),
    Merchant("Top Achat", "FR", "retailer", ("topachat.com",), r"top ?achat"),
    Merchant("Apple", "EU", "retailer", ("apple.com",), r"^apple(?: store)?$"),
    Merchant("Samsung", "EU", "retailer", ("samsung.com",), r"^samsung(?: shop| store)?$"),
    Merchant("MediaMarkt", "DE", "retailer", ("mediamarkt.de", "mediamarkt.es", "mediamarkt.it", "mediamarkt.at"),
             r"media ?markt(?! tarifwelt)"),
    Merchant("Saturn", "DE", "retailer", ("saturn.de",), r"\bsaturn\b"),
    Merchant("Otto", "DE", "retailer", ("otto.de",), r"\botto\b"),
    Merchant("Kaufland", "DE", "marketplace", ("kaufland.de",), r"\bkaufland\b"),
    Merchant("Cyberport", "DE", "retailer", ("cyberport.de",), r"\bcyberport\b"),
    Merchant("Notebooksbilliger", "DE", "retailer", ("notebooksbilliger.de",), r"notebooksbilliger|\bnbb\b"),
    Merchant("Galaxus", "DE", "retailer", ("galaxus.de", "galaxus.fr"), r"\bgalaxus\b"),
    Merchant("Coolblue", "EU", "retailer", ("coolblue.",), r"\bcoolblue\b"),
    Merchant("PcComponentes", "ES", "retailer", ("pccomponentes.",), r"pc ?componentes"),
    Merchant("Currys", "UK", "retailer", ("currys.co.uk",), r"\bcurrys\b", priority=False),
    Merchant("Argos", "UK", "retailer", ("argos.co.uk",), r"\bargos\b", priority=False),
    Merchant("eBay", "EU", "marketplace", ("ebay.",), r"\bebay\b", priority=False),
]
_ALIASES = [(m, re.compile(m.aliases, re.IGNORECASE)) for m in MERCHANTS]


def _host(url: str | None) -> str:
    return (urlparse(url or "").hostname or "").lower()


def identify_merchant(deal: dict) -> dict:
    """Marchand canonique d'après l'URL résolue, le domaine Pepper puis le nom saisi."""
    hosts = [deal.get("merchant_host") or _host(deal.get("merchant_url")), (deal.get("link_host") or "").lower()]
    merchant = None
    for host in filter(None, hosts):
        merchant = next((m for m in MERCHANTS if any(h in host for h in m.hosts)), None)
        if merchant:
            break
    if merchant is None and deal.get("merchant"):
        merchant = next((m for m, regex in _ALIASES if regex.search(deal["merchant"])), None)
    if merchant is None:
        return {"merchant_name": deal.get("merchant"), "merchant_country": None,
                "merchant_kind": None, "priority_merchant": False}
    return {
        "merchant_name": merchant.name,
        "merchant_country": merchant.country,
        "merchant_kind": merchant.kind,
        "priority_merchant": merchant.priority,
    }
