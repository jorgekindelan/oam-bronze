from __future__ import annotations

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


def build_async_client(*, user_agent: str = "oam-bronze/0.1", timeout_s: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_s),
        headers={"User-Agent": user_agent, "Accept": "*/*"},
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
