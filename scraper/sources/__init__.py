"""Sources d'annonces : chaque source expose ``id``, ``fetch(client)`` et ``enrich(client, deal)``."""

from .pepper_source import SITES, PepperSource, default_sources

__all__ = ["SITES", "PepperSource", "default_sources"]
