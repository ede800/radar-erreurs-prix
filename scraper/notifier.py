"""Alertes instantanées Telegram / Discord pour les erreurs de prix (score > 80).

Configuration (variables d'environnement ou fichier ``.env`` à la racine) :
  DISCORD_WEBHOOK_URL   webhook d'un salon Discord ;
  TELEGRAM_BOT_TOKEN    jeton du bot Telegram, avec TELEGRAM_CHAT_ID (chat ou canal) ;
  ALERT_MIN_SCORE       seuil (exclusif) du score, 80 par défaut.

Déduplication : ``history.alerts`` (SQLite) + champ ``alerted`` dans deals.json, jamais
deux alertes pour la même annonce au même prix (une nouvelle baisse ré-alerte).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .glitch_engine import ALERT_MAX_AGE_MINUTES, CRITICAL_SCORE, should_alert

MAX_ALERTS_PER_RUN = 10   # garde-fou si un flux renvoie d'un coup beaucoup d'annonces
SYMBOLS = {"EUR": "€", "GBP": "£", "USD": "$", "PLN": "zł"}
SELLER_LABELS = {
    "direct": "Officiel (vendu et expédié par l'enseigne)",
    "retailer": "Enseigne réputée",
    "marketplace": "Marketplace (vendeur non précisé)",
    "third_party": "Vendeur tiers",
    "dropship": "Dropshipping",
    "unknown": "Inconnu",
}


def load_env_file(path: str | Path) -> None:
    """Charge un fichier ``.env`` simple (CLE=valeur) sans écraser l'environnement existant."""
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def money(value: float | None, currency: str | None) -> str:
    if value is None:
        return "?"
    if value == 0:
        return "Gratuit"
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"{text} {SYMBOLS.get(currency or 'EUR', currency or '')}"


def summary_lines(deal: dict) -> list[str]:
    currency = deal.get("currency")
    lines = [f"🚨 GLITCH SCORE : {deal.get('score', 0)}/100"]
    price_line = f"💰 {money(deal.get('price'), currency)}"
    if deal.get("market_price"):
        label = "base catalogue" if deal.get("market_source") == "catalog" else "prix marché"
        price_line += f" vs {label} {money(deal['market_price'], currency)}"
        if deal.get("real_discount") is not None:
            price_line += f" (−{deal['real_discount']} %)"
    elif deal.get("original_price"):
        price_line += f" (barré {money(deal['original_price'], currency)}, non vérifié)"
    lines.append(price_line)
    if deal.get("savings"):
        extra = f"💸 Économie : {money(deal['savings'], currency)}"
        if deal.get("resale_margin") and deal["resale_margin"] > 0:
            extra += f" · marge de revente ≈ {money(deal['resale_margin'], currency)}"
        lines.append(extra)
    merchant = deal.get("merchant") or deal.get("marketplace") or "?"
    lines.append(f"🏪 {merchant} · {SELLER_LABELS.get(deal.get('seller', 'unknown'), '?')}")
    if deal.get("reasons"):
        lines.append("🏷️ " + " · ".join(deal["reasons"]))
    return lines


def link_lines(deal: dict) -> list[tuple[str, str]]:
    links = [("Voir l'annonce", deal["link"])]
    if deal.get("product_url"):
        links.append(("Page produit", deal["product_url"]))
    if deal.get("cart_url"):
        links.append(("🛒 Ajout direct au panier", deal["cart_url"]))
    for cc, url in (deal.get("amazon_eu") or {}).items():
        links.append((f"Amazon.{cc}", url))
    return links


def discord_payload(deal: dict) -> dict:
    description = "\n".join(summary_lines(deal))
    description += "\n" + " · ".join(f"[{label}]({url})" for label, url in link_lines(deal))
    embed = {
        "title": deal["title"][:256],
        "url": deal["link"],
        "description": description[:4000],
        "color": 0xFF3B30 if deal.get("certified") else 0xFF9F0A,
    }
    if deal.get("image"):
        embed["thumbnail"] = {"url": deal["image"]}
    return {"username": "Radar erreurs de prix", "embeds": [embed]}


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def telegram_payload(deal: dict, chat_id: str) -> dict:
    text = f"<b>{_esc(deal['title'])}</b>\n" + "\n".join(map(_esc, summary_lines(deal)))
    text += "\n" + " · ".join(f'<a href="{_esc(url)}">{_esc(label)}</a>' for label, url in link_lines(deal))
    return {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}


class Notifier:
    def __init__(self, env: Mapping[str, str] | None = None):
        env = os.environ if env is None else env
        self.discord = (env.get("DISCORD_WEBHOOK_URL") or "").strip()
        self.token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        self.chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
        self.threshold = int(env.get("ALERT_MIN_SCORE") or CRITICAL_SCORE)
        self.max_age_minutes = int(env.get("ALERT_MAX_AGE_MINUTES") or ALERT_MAX_AGE_MINUTES)
        if self.token and not self.chat_id:
            print("[alerte] TELEGRAM_BOT_TOKEN défini sans TELEGRAM_CHAT_ID : Telegram ignoré.", file=sys.stderr)
            self.token = ""

    @property
    def enabled(self) -> bool:
        return bool(self.discord or self.token)

    async def _send(self, client: httpx.AsyncClient, deal: dict) -> bool:
        delivered = False
        calls = []
        if self.discord:
            calls.append(("Discord", self.discord, discord_payload(deal)))
        if self.token:
            calls.append(("Telegram", f"https://api.telegram.org/bot{self.token}/sendMessage",
                          telegram_payload(deal, self.chat_id)))
        for name, url, payload in calls:
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                delivered = True
            except httpx.HTTPError as exc:
                # Jamais l'URL dans les logs : elle contient le secret du webhook / du bot.
                print(f"[alerte] {name} en échec ({type(exc).__name__}) pour {deal['id']}", file=sys.stderr)
        return delivered

    async def notify(self, client: httpx.AsyncClient, deals: list[dict], history, now: datetime | None = None) -> list[dict]:
        """Notifie les annonces qualifiées non encore alertées ; renvoie celles envoyées."""
        if not self.enabled:
            return []
        now = now or datetime.now(timezone.utc)
        candidates = [d for d in deals if should_alert(d, self.threshold, self.max_age_minutes)
                      and not history.already_alerted(d["id"], d.get("price"))]
        candidates.sort(key=lambda d: d.get("score", 0), reverse=True)
        sent = []
        for deal in candidates[:MAX_ALERTS_PER_RUN]:
            if await self._send(client, deal):
                history.mark_alerted(deal["id"], deal.get("price"), now)
                deal["alerted"] = now.isoformat(timespec="seconds")
                sent.append(deal)
        history.commit()
        return sent
