"""Daemon temps réel : ``python -m scraper.daemon`` (Ctrl+C pour arrêter).

Interroge les flux toutes les ``POLL_INTERVAL`` secondes (20 par défaut, minimum 10),
garde la connexion HTTP/2 et la mémoire SQLite ouvertes entre deux cycles, et réécrit
``docs/data/deals.json`` à chaque changement (servi en local par
``python -m http.server -d docs 8000``).
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time
from datetime import datetime

from .history import History
from .merchant_apis import providers_from_env
from .net import make_client
from .notifier import Notifier, load_env_file
from .pipeline import ROOT, Radar, Settings
from .price_verifier import PriceVerifier
from .sources import default_sources

MIN_INTERVAL = 10.0


def poll_interval() -> float:
    try:
        value = float(os.environ.get("POLL_INTERVAL", "20"))
    except ValueError:
        value = 20.0
    return max(value, MIN_INTERVAL)


async def run_forever(max_cycles: int | None = None) -> None:
    interval = poll_interval()
    settings = Settings(max_enrich=int(os.environ.get("MAX_ENRICH_PER_CYCLE", "15")))
    with History(settings.db_path) as history:
        async with make_client() as client:
            radar = Radar(
                client, default_sources(), history,
                verifier=PriceVerifier(providers_from_env(os.environ)),
                notifier=Notifier(),
                settings=settings,
            )
            print(f"📡 Radar démarré : cycle toutes les {interval:.0f} s "
                  f"(alertes {'activées' if radar.notifier.enabled else 'désactivées'}).")
            cycles = 0
            failures = 0
            while max_cycles is None or cycles < max_cycles:
                started = time.monotonic()
                try:
                    result = await radar.cycle()
                    failures = 0 if result["ok"] else failures + 1
                    stamp = datetime.now().strftime("%H:%M:%S")
                    flags = f"{result.get('critical', 0)} critiques"
                    if result["sent"]:
                        flags += f", 🚨 {len(result['sent'])} alerte(s)"
                    print(f"[{stamp}] {result.get('count', 0)} annonces, {flags}"
                          f"{' (mis à jour)' if result['changed'] else ''}")
                except Exception as exc:          # le daemon ne doit jamais mourir sur un cycle
                    failures += 1
                    print(f"[cycle] erreur : {exc!r}", file=sys.stderr)
                cycles += 1
                # Backoff si toutes les sources échouent (blocage probable), jitter sinon.
                pause = interval * min(2 ** failures, 16) if failures else interval
                pause = max(pause - (time.monotonic() - started), 1.0) + random.uniform(0, 3)
                if max_cycles is None or cycles < max_cycles:
                    await asyncio.sleep(pause)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):      # emojis sur une console Windows cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        print("\nArrêt du radar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
