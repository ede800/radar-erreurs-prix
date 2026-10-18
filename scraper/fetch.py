"""Récupère les erreurs de prix signalées par les communautés de bons plans
(réseau Pepper : Dealabs, mydealz, Preisjäger, HotUKDeals) et les fusionne
dans docs/data/deals.json, servi par le site GitHub Pages.

Uniquement la bibliothèque standard : aucune dépendance à installer.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "docs" / "data" / "deals.json"

USER_AGENT = (
    "Mozilla/5.0 (compatible; radar-erreurs-prix/1.0; "
    "+https://github.com/ede800/radar-erreurs-prix)"
)

SOURCES = [
    {"id": "dealabs", "name": "Dealabs", "country": "FR",
     "url": "https://www.dealabs.com/rss/groupe/erreur-de-prix"},
    {"id": "mydealz", "name": "mydealz", "country": "DE",
     "url": "https://www.mydealz.de/rss/gruppe/preisfehler"},
    {"id": "preisjaeger", "name": "Preisjäger", "country": "AT",
     "url": "https://www.preisjaeger.at/rss/gruppe/preisfehler"},
    {"id": "hotukdeals", "name": "HotUKDeals", "country": "UK",
     "url": "https://www.hotukdeals.com/rss/tag/price-glitch"},
]

# Ordre important : le premier motif trouvé dans le nom du marchand l'emporte.
MARKETPLACES = [
    ("amazon", "Amazon"),
    ("cdiscount", "Cdiscount"),
    ("fnac", "Fnac"),
    ("darty", "Darty"),
    ("ebay", "eBay"),
    ("aliexpress", "AliExpress"),
    ("rakuten", "Rakuten"),
    ("boulanger", "Boulanger"),
    ("leclerc", "Leclerc"),
    ("carrefour", "Carrefour"),
    ("auchan", "Auchan"),
    ("media markt", "MediaMarkt"),
    ("mediamarkt", "MediaMarkt"),
    ("saturn", "Saturn"),
    ("otto", "Otto"),
    ("kaufland", "Kaufland"),
    ("argos", "Argos"),
    ("currys", "Currys"),
    ("walmart", "Walmart"),
]

ORIGINAL_PRICE_RE = re.compile(
    r"(?:au lieu de|prix (?:habituel|normal|de base)|statt|uvp|was|rrp|normally)"
    r"\s*:?\s*(?:de\s*)?([€£$]?\s?\d[\d.,\s]*\s?(?:€|£|\$|eur)?)",
    re.IGNORECASE,
)

NS = {"pepper": "http://www.pepper.com/rss", "media": "http://search.yahoo.com/mrss/"}

RETENTION_DAYS = 60
MAX_DEALS = 3000


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


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def parse_feed(xml_bytes: bytes, source: dict) -> list[dict]:
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
        description = clean_text(body)
        image_el = item.find("media:content", NS)
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            published_iso = published.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            published_iso = None
        original = find_original_price(clean_text(body, limit=10_000), price)
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
            "description": description,
            "source": source["id"],
            "country": source["country"],
            "published": published_iso,
        })
    return deals


def load_existing() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"deals": []}


def merge(existing: list[dict], fresh: list[dict], now: datetime) -> list[dict]:
    by_id = {deal["id"]: deal for deal in existing}
    now_iso = now.isoformat(timespec="seconds")
    for deal in fresh:
        previous = by_id.get(deal["id"])
        deal["first_seen"] = previous["first_seen"] if previous else now_iso
        by_id[deal["id"]] = deal
    cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat()
    kept = [d for d in by_id.values() if (d.get("published") or d["first_seen"]) >= cutoff]
    kept.sort(key=lambda d: d.get("published") or d["first_seen"], reverse=True)
    return kept[:MAX_DEALS]


def main() -> int:
    now = datetime.now(timezone.utc)
    data = load_existing()
    fresh: list[dict] = []
    statuses = []
    for source in SOURCES:
        try:
            items = parse_feed(fetch(source["url"]), source)
            fresh.extend(items)
            statuses.append({**source, "ok": True, "count": len(items)})
            print(f"[ok] {source['name']}: {len(items)} annonces")
        except Exception as exc:  # une source en panne ne doit pas bloquer les autres
            statuses.append({**source, "ok": False, "error": str(exc)[:200]})
            print(f"[erreur] {source['name']}: {exc}", file=sys.stderr)

    if not any(s["ok"] for s in statuses):
        print("Aucune source disponible, données inchangées.", file=sys.stderr)
        return 1

    deals = merge(data.get("deals", []), fresh, now)
    if deals == data.get("deals") and statuses == data.get("sources"):
        print("Aucun changement.")
        return 0
    output = {"updated": now.isoformat(timespec="seconds"), "sources": statuses, "deals": deals}
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(deals)} annonces enregistrées dans {DATA_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
