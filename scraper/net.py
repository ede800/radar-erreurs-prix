"""Client HTTP asynchrone partagé : pool de connexions, HTTP/2, en-têtes navigateur,
rotation de User-Agents et retry avec backoff exponentiel (429 / 403 / 5xx)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import httpx

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]
RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
LIMITS = httpx.Limits(max_keepalive_connections=20, max_connections=50)


def browser_headers(user_agent: str | None = None) -> dict[str, str]:
    return {
        "User-Agent": user_agent or random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,de;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br" if _has_brotli() else "gzip, deflate",
        "Cache-Control": "no-cache",
    }


def _has_brotli() -> bool:
    try:
        import brotli  # noqa: F401
    except ImportError:
        try:
            import brotlicffi  # noqa: F401
        except ImportError:
            return False
    return True


def _has_h2() -> bool:
    try:
        import h2  # noqa: F401
    except ImportError:
        return False
    return True


def make_client(**kwargs) -> httpx.AsyncClient:
    """Client avec pool de connexions ; HTTP/2 si le paquet ``h2`` est installé."""
    options = {
        "limits": LIMITS,
        "timeout": httpx.Timeout(20.0, connect=10.0),
        "follow_redirects": True,
        "http2": _has_h2(),
        "headers": browser_headers(),
    }
    options.update(kwargs)
    return httpx.AsyncClient(**options)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    try:
        return min(float(value), 60.0) if value else None
    except ValueError:
        return None


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> httpx.Response:
    """GET avec un nouveau User-Agent à chaque tentative et un backoff 1 s, 2 s, 4 s… (+ jitter).

    Renvoie la dernière réponse (y compris 304) ; lève ``httpx.HTTPStatusError`` si toutes
    les tentatives échouent sur un statut d'erreur, ou l'erreur réseau d'origine.
    """
    last_error: Exception | None = None
    for attempt in range(attempts):
        request_headers = {"User-Agent": random.choice(USER_AGENTS), **(headers or {})}
        try:
            response = await client.get(url, headers=request_headers, params=params)
        except httpx.TransportError as exc:
            last_error = exc
            response = None
        if response is not None:
            if response.status_code not in RETRY_STATUSES:
                if response.status_code >= 400:
                    response.raise_for_status()
                return response
            last_error = httpx.HTTPStatusError(
                f"HTTP {response.status_code}", request=response.request, response=response
            )
        if attempt < attempts - 1:
            delay = (_retry_after(response) if response is not None else None) or base_delay * 2 ** attempt
            await sleep(delay + random.uniform(0, base_delay / 2))
    assert last_error is not None
    raise last_error
