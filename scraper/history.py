"""Mémoire SQLite (``data/history.db``) : annonces vues, historique des prix, alertes envoyées.

  - ``seen``   : chaque annonce rencontrée (même écartée) pour ne pas la ré-analyser ;
  - ``prices`` : prix observés par produit (ASIN ou titre normalisé) → plus bas historique ;
  - ``alerts`` : (annonce, prix) déjà notifiés → jamais deux alertes au même prix.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    deal_id TEXT PRIMARY KEY,
    first_seen TEXT NOT NULL,
    kept INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS prices (
    product_key TEXT NOT NULL,
    deal_id TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT,
    seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_by_product ON prices (product_key, currency);
CREATE INDEX IF NOT EXISTS prices_by_deal ON prices (deal_id);
CREATE TABLE IF NOT EXISTS alerts (
    deal_id TEXT NOT NULL,
    price REAL,
    sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_by_deal ON alerts (deal_id);
"""

_STOPWORDS = {"de", "du", "la", "le", "les", "des", "et", "en", "pour", "avec", "the", "and", "for",
              "with", "mit", "und", "für", "der", "die", "das", "go", "gb", "to", "tb"}


def product_key(deal: dict) -> str:
    """Clé produit : l'ASIN s'il est connu, sinon les premiers mots significatifs du titre."""
    if deal.get("asin"):
        return f"asin:{deal['asin']}"
    title = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", deal.get("title") or "")
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.findall(r"[a-z0-9]+", title) if w not in _STOPWORDS]
    return "title:" + " ".join(words[:10])


class History:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def __enter__(self) -> "History":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- Annonces vues ---------------------------------------------------------
    def is_known(self, deal_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM seen WHERE deal_id = ?", (deal_id,)).fetchone() is not None

    def mark_seen(self, deal_id: str, now: datetime, kept: bool) -> None:
        self.conn.execute(
            "INSERT INTO seen (deal_id, first_seen, kept) VALUES (?, ?, ?) "
            "ON CONFLICT(deal_id) DO UPDATE SET kept = max(kept, excluded.kept)",
            (deal_id, now.isoformat(timespec="seconds"), int(kept)),
        )

    # --- Mémoire de prix -------------------------------------------------------
    def lowest_price(self, key: str, currency: str | None, exclude_deal: str | None = None) -> float | None:
        row = self.conn.execute(
            "SELECT min(price) FROM prices WHERE product_key = ? AND currency IS ? AND deal_id IS NOT ? AND price > 0",
            (key, currency, exclude_deal),
        ).fetchone()
        return row[0] if row else None

    def record_price(self, deal: dict, now: datetime) -> None:
        price = deal.get("price")
        if price is None:
            return
        last = self.conn.execute(
            "SELECT price FROM prices WHERE deal_id = ? ORDER BY rowid DESC LIMIT 1", (deal["id"],)
        ).fetchone()
        if last and last[0] == price:
            return
        self.conn.execute(
            "INSERT INTO prices (product_key, deal_id, price, currency, seen_at) VALUES (?, ?, ?, ?, ?)",
            (product_key(deal), deal["id"], price, deal.get("currency"), now.isoformat(timespec="seconds")),
        )

    def price_points(self, key: str, currency: str | None, limit: int = 12) -> list[dict]:
        """Derniers prix observés pour ce produit (toutes annonces confondues), du plus ancien au plus récent."""
        rows = self.conn.execute(
            "SELECT price, seen_at, deal_id FROM prices WHERE product_key = ? AND currency IS ? "
            "ORDER BY seen_at DESC, rowid DESC LIMIT ?",
            (key, currency, limit),
        ).fetchall()
        return [{"price": p, "at": at, "deal": d} for p, at, d in reversed(rows)]

    # --- Alertes ---------------------------------------------------------------
    def already_alerted(self, deal_id: str, price: float | None) -> bool:
        """Vrai si l'annonce a déjà été notifiée à ce prix ou moins cher."""
        rows = self.conn.execute("SELECT price FROM alerts WHERE deal_id = ?", (deal_id,)).fetchall()
        if not rows:
            return False
        if price is None:
            return True
        return any(p is None or p <= price + 0.005 for (p,) in rows)

    def mark_alerted(self, deal_id: str, price: float | None, now: datetime) -> None:
        self.conn.execute(
            "INSERT INTO alerts (deal_id, price, sent_at) VALUES (?, ?, ?)",
            (deal_id, price, now.isoformat(timespec="seconds")),
        )

    def prune(self, now: datetime, days: int = 120) -> None:
        cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
        self.conn.execute("DELETE FROM prices WHERE seen_at < ?", (cutoff,))
        self.conn.execute("DELETE FROM seen WHERE first_seen < ?", (cutoff,))
        self.conn.execute("DELETE FROM alerts WHERE sent_at < ?", (cutoff,))

    def commit(self) -> None:
        self.conn.commit()
