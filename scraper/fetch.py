"""Exécution unique du radar (GitHub Action de secours) : ``python -m scraper.fetch``.

Pour la surveillance temps réel, utiliser le daemon : ``python -m scraper.daemon``.
"""

from __future__ import annotations

import asyncio
import os
import sys

from .history import History
from .merchant_apis import providers_from_env
from .net import make_client
from .notifier import Notifier, load_env_file
from .pipeline import ROOT, Radar, Settings, merge  # noqa: F401  (merge ré-exporté)
from .price_verifier import PriceVerifier
from .sources import default_sources
from .sources.pepper_source import (  # noqa: F401  (API v1 conservée pour compatibilité)
    classify_marketplace,
    clean_text,
    find_original_price,
    parse_feed,
    parse_price,
)


async def run_once(settings: Settings | None = None) -> int:
    settings = settings or Settings()
    with History(settings.db_path) as history:
        async with make_client() as client:
            radar = Radar(
                client, default_sources(), history,
                verifier=PriceVerifier(providers_from_env(os.environ)),
                notifier=Notifier(),
                settings=settings,
            )
            result = await radar.cycle()
    for status in result["statuses"]:
        label = f"{status['count']} annonces" if status["ok"] else status["error"]
        print(f"[{'ok' if status['ok'] else 'erreur'}] {status['name']}: {label}")
    if not result["ok"]:
        print("Aucune source disponible, données inchangées.", file=sys.stderr)
        return 1
    print(f"{result['count']} annonces, dont {result['critical']} critiques ; "
          f"{len(result['sent'])} alerte(s) envoyée(s) ; "
          f"{'deals.json mis à jour' if result['changed'] else 'aucun changement'}.")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):      # emojis sur une console Windows cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    return asyncio.run(run_once())


if __name__ == "__main__":
    sys.exit(main())
