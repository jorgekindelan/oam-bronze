from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Mapping, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter


@dataclass(frozen=True)
class HttpFetchResult:
    final_url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes


class _RetryTransport(httpx.AsyncBaseTransport):
    """Wraps any async transport with exponential-backoff retry on network-level errors."""

    def __init__(self, wrapped: httpx.AsyncBaseTransport, *, max_attempts: int = 4) -> None:
        self._wrapped = wrapped
        self._max_attempts = max_attempts

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        last_exc: BaseException = RuntimeError("unreachable")
        wait = 1.0
        for attempt in range(self._max_attempts):
            try:
                return await self._wrapped.handle_async_request(request)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < self._max_attempts - 1:
                    await asyncio.sleep(wait + random.uniform(0, 0.5))
                    wait = min(wait * 2, 30.0)
        raise last_exc

    async def aclose(self) -> None:
        await self._wrapped.aclose()


def build_async_client(
    *,
    timeout_s: float = 30.0,
    headers: Optional[Mapping[str, str]] = None,
    follow_redirects: bool = True,
    max_retries: int = 4,
) -> httpx.AsyncClient:
    """Return an AsyncClient with transparent retry on timeout/transport errors."""
    transport = _RetryTransport(httpx.AsyncHTTPTransport(), max_attempts=max_retries)
    return httpx.AsyncClient(
        follow_redirects=follow_redirects,
        timeout=httpx.Timeout(timeout_s),
        headers=dict(headers) if headers else {},
        transport=transport,
    )


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.TransportError)),
    wait=wait_exponential_jitter(initial=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
async def fetch_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
) -> HttpFetchResult:
    resp = await client.get(url, headers=headers)
    return HttpFetchResult(
        final_url=str(resp.url),
        status_code=resp.status_code,
        headers={k.lower(): v for k, v in resp.headers.items()},
        content=resp.content,
    )
