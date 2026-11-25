"""Async HTTP with per-host pacing and retries (ARCHITECTURE.md §5.3)."""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from . import __version__

USER_AGENT = f"litkb/{__version__} (literature review tool)"


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


class PoliteClient:
    """httpx.AsyncClient wrapper: one request at a time per host, `delay_s` apart, with retries."""

    def __init__(self, delay_s: float = 1.0, timeout_s: float = 60, headers: dict | None = None,
                 max_attempts: int = 3, transport: httpx.AsyncBaseTransport | None = None):
        self.delay_s = delay_s
        self.max_attempts = max_attempts
        self._client = httpx.AsyncClient(
            timeout=timeout_s, follow_redirects=True, transport=transport,
            headers={"User-Agent": USER_AGENT, **(headers or {})})
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _pace(self, url: str) -> None:
        host = urlsplit(url).netloc
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last.get(host, 0) + self.delay_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()

    async def request(self, method: str, url: str, *, raise_for_status: bool = True,
                      **kw) -> httpx.Response:
        async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self.max_attempts), reraise=True,
                wait=wait_exponential(multiplier=self.delay_s or 0.5, max=30),
                retry=retry_if_exception(_retryable)):
            with attempt:
                await self._pace(url)
                r = await self._client.request(method, url, **kw)
                if r.status_code in (429, 500, 502, 503, 504) or (raise_for_status and r.is_error):
                    r.raise_for_status()
                return r
        raise AssertionError("unreachable")

    async def get(self, url: str, **kw) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def get_json(self, url: str, **kw):
        return (await self.get(url, **kw)).json()

    async def post(self, url: str, **kw) -> httpx.Response:
        return await self.request("POST", url, **kw)
