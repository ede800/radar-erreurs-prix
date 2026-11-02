"""Un cycle du radar : ingestion → enrichissement → vérification marché → mémoire de prix
→ scoring → alertes → écriture de ``docs/data/deals.json``.

Utilisé à l'identique par le daemon (toutes les 20 s) et par la GitHub Action (une fois).
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import glitch_engine
from .history import History, product_key
from .merchant_apis import checkout_links, extract_asin
from .notifier import Notifier
from .price_verifier import PriceVerifier
from .reference_catalog import lookup as catalog_lookup

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 2

# Champs calculés lors d'un cycle précédent à conserver quand le flux renvoie l'annonce.
CARRIED_FIELDS = (
    "first_seen", "temperature", "next_best_price", "expired", "link_host", "enriched_at",
    "market_median", "market_floor", "market_sources", "asin", "ean", "product_url", "cart_url", "alerted",
    "live_price", "live_available", "live_brand", "live_url", "live_corrected",
)


@dataclass
class Settings:
    data_file: Path = ROOT / "docs" / "data" / "deals.json"
    db_path: Path = ROOT / "data" / "history.db"
    retention_days: int = 60
    max_deals: int = 3000
    enrich_window_hours: float = 48
    enrich_ttl_minutes: float = 15
    max_enrich: int = 40
    enrich_concurrency: int = 6


def load_data(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"deals": []}


def merge(existing: list[dict], fresh: list[dict], now: datetime, retention_days: int = 60,
          max_deals: int = 3000) -> list[dict]:
    by_id = {deal["id"]: deal for deal in existing}
    now_iso = now.isoformat(timespec="seconds")
    for deal in fresh:
        previous = by_id.get(deal["id"])
        if previous:
            for key in CARRIED_FIELDS:
                if key in previous and deal.get(key) is None:
                    deal[key] = previous[key]
            if previous.get("stream") == "glitch":
                deal["stream"] = "glitch"
            deal["glitch_keyword"] = deal.get("glitch_keyword") or previous.get("glitch_keyword", False)
        if not deal.get("first_seen"):
            deal["first_seen"] = now_iso
        by_id[deal["id"]] = deal
    cutoff = (now - timedelta(days=retention_days)).isoformat()
    kept = [d for d in by_id.values() if (d.get("published") or d["first_seen"]) >= cutoff]
    kept.sort(key=lambda d: d.get("published") or d["first_seen"], reverse=True)
    return kept[:max_deals]


def _age_hours(deal: dict, now: datetime) -> float:
    return glitch_engine.hours_since(deal.get("published") or deal.get("first_seen"), now) or 0.0


def needs_enrichment(deal: dict, now: datetime, settings: Settings) -> bool:
    """Toute annonce jamais lue est enrichie une fois, quel que soit son âge ; ensuite on ne
    rafraîchit (votes, expiration) que les annonces des dernières ``enrich_window_hours``."""
    last = deal.get("enriched_at")
    if not last:
        return True
    if _age_hours(deal, now) > settings.enrich_window_hours:
        return False
    return (glitch_engine.hours_since(last, now) or 0) * 60 >= settings.enrich_ttl_minutes


class Radar:
    def __init__(self, client: httpx.AsyncClient, sources: list, history: History,
                 verifier: PriceVerifier | None = None, notifier: Notifier | None = None,
                 settings: Settings | None = None):
        self.client = client
        self.sources = {s.id: s for s in sources}
        self.history = history
        self.verifier = verifier or PriceVerifier()
        self.notifier = notifier or Notifier({})
        self.settings = settings or Settings()
        self.data = load_data(self.settings.data_file)

    async def _ingest(self) -> tuple[list[dict], list[dict]]:
        results = await asyncio.gather(*(s.fetch(self.client) for s in self.sources.values()),
                                       return_exceptions=True)
        fresh, statuses = [], []
        for source, result in zip(self.sources.values(), results):
            site = source.site
            status = {"id": site["id"], "name": site["name"], "country": site["country"],
                      "url": site["base"] + site["glitch"]}
            if isinstance(result, BaseException):
                statuses.append({**status, "ok": False, "error": str(result)[:200] or type(result).__name__})
                print(f"[erreur] {site['name']}: {result!r}", file=sys.stderr)
            else:
                fresh.extend(result)
                statuses.append({**status, "ok": True, "count": len(result)})
        return fresh, statuses

    def _prefilter(self, fresh: list[dict], known_ids: set[str]) -> list[dict]:
        """Flux « nouveautés » : on ignore d'emblée ce qui ne peut pas être une erreur de prix."""
        kept = []
        for deal in fresh:
            if deal["id"] in known_ids or deal.get("stream") == "glitch" or deal.get("glitch_keyword"):
                kept.append(deal)
            elif self.history.is_known(deal["id"]):
                continue                                   # déjà vue et écartée
            elif glitch_engine.find_brand(deal["title"]):
                kept.append(deal)
            else:
                self.history.mark_seen(deal["id"], datetime.now(timezone.utc), kept=False)
        return kept

    async def _enrich(self, deals: list[dict], now: datetime) -> None:
        todo = [d for d in deals if d["source"] in self.sources and needs_enrichment(d, now, self.settings)]
        # Jamais enrichies d'abord, puis mots-clés d'erreur, puis les plus récentes.
        todo.sort(key=lambda d: (d.get("enriched_at") is not None, not d.get("glitch_keyword"), _age_hours(d, now)))
        todo = todo[: self.settings.max_enrich]
        semaphore = asyncio.Semaphore(self.settings.enrich_concurrency)
        stamp = now.isoformat(timespec="seconds")

        async def one(deal: dict) -> None:
            async with semaphore:
                try:
                    info = await self.sources[deal["source"]].enrich(self.client, deal)
                except httpx.HTTPError as exc:
                    print(f"[page] {deal['id']}: {type(exc).__name__}", file=sys.stderr)
                    return
                deal.update(info)
                deal["enriched_at"] = stamp
                # Résolution JSON-LD (3 s) réservée aux annonces prometteuses sans prix marché.
                promising = bool(glitch_engine.find_brand(deal.get("title", "")) or deal.get("glitch_keyword"))
                no_reference = not deal.get("next_best_price") and not catalog_lookup(
                    deal.get("title", ""), deal.get("currency") or "EUR")
                check, live = await self.verifier.verify(self.client, deal, resolve_links=promising and no_reference)
                deal.update(check.as_fields())
                deal.update(live)

        await asyncio.gather(*(one(d) for d in todo))

    async def cycle(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        settings = self.settings
        existing = self.data.get("deals", [])
        known_ids = {d["id"] for d in existing}

        fresh, statuses = await self._ingest()
        if not any(s["ok"] for s in statuses):
            return {"ok": False, "changed": False, "sent": [], "statuses": statuses}

        fresh = self._prefilter(fresh, known_ids)
        deals = merge(existing, fresh, now, settings.retention_days, settings.max_deals)
        for deal in deals:
            if not deal.get("asin"):
                deal["asin"] = extract_asin(*(deal.get("merchant_links") or []), deal.get("title"),
                                            deal.get("description"))
        await self._enrich(deals, now)

        kept = []
        fresh_ids = {d["id"] for d in fresh}
        for deal in deals:
            key = product_key(deal)
            deal["history_low"] = self.history.lowest_price(key, deal.get("currency"), exclude_deal=deal["id"])
            glitch_engine.qualify(deal, now)
            deal.update(checkout_links(deal))
            if deal.get("stream") == "fresh" and not glitch_engine.is_candidate(deal, now):
                self.history.mark_seen(deal["id"], now, kept=False)
                continue
            if deal["id"] in fresh_ids:
                self.history.mark_seen(deal["id"], now, kept=True)
                self.history.record_price(deal, now)
            kept.append(deal)
        self.history.prune(now)
        self.history.commit()

        sent = await self.notifier.notify(self.client, kept, self.history, now)

        changed = kept != self.data.get("deals") or statuses != self.data.get("sources")
        if changed:
            self.data = {"version": SCHEMA_VERSION, "updated": now.isoformat(timespec="seconds"),
                         "sources": statuses, "deals": kept}
            settings.data_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = settings.data_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(settings.data_file)
        return {"ok": True, "changed": changed, "sent": sent, "statuses": statuses, "count": len(kept),
                "critical": sum(1 for d in kept if d.get("level") == "critical")}
