from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from oam.connectors.base import BaseConnector
from oam.connectors.registry import register
from oam.core.ids import make_ids
from oam.core.logging import get_logger
from oam.core.time import now_utc
from oam.models.discovery import DiscoveryRecord
from oam.models.document import DocumentRecord

from oam.connectors.nl_afm_inside_info_parsers import (
    parse_detail_html,
    parse_list_page,
)

logger = get_logger("oam.nl.afm_inside_info")

DEFAULT_REGISTER_URL = "https://www.afm.nl/en/sector/registers/meldingenregisters/openbaarmaking-voorwetenschap"
DEFAULT_DETAIL_TEMPLATE = "https://www.afm.nl/en/sector/registers/meldingenregisters/openbaarmaking-voorwetenschap/details?id={record_id}"


@register
class NLAFMInsideInfo(BaseConnector):
    country_code = "NL"
    source_code = "AFM_INSIDE_INFO"
    source_name = "AFM Register publication of inside information (openbaarmaking voorwetenschap)"

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if state is None:
            return {"watermark_published_at_utc": None}
        state.setdefault("watermark_published_at_utc", None)
        return state

    async def _throttle(self) -> None:
        rps = float(self.config.get("throttle_rps", 1.5))
        if rps > 0:
            await asyncio.sleep(1.0 / rps)

    async def discover(
        self,
        *,
        crawl_run_id: str,
        date_from: datetime,
        date_to: datetime,
        checkpoint: Optional[dict[str, Any]],
    ) -> AsyncIterator[DiscoveryRecord]:
        extra = dict(self.config.get("extra", {}) or {})
        register_url = str(extra.get("register_url") or DEFAULT_REGISTER_URL)
        tz = ZoneInfo(str(extra.get("timezone") or "Europe/Amsterdam"))
        base_url = "https://www.afm.nl"

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        timeout_s = int(self.config.get("timeout_s", 60))

        seen: set[str] = set()
        queue: list[str] = [register_url]
        pages_visited = 0

        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
            while queue:
                page_url = queue.pop(0)
                if page_url in seen:
                    continue
                seen.add(page_url)
                pages_visited += 1

                await self._throttle()
                r = await client.get(page_url)
                r.raise_for_status()

                hits, next_page = parse_list_page(r.text, base_url=base_url, source_tz=tz)
                if pages_visited == 1:
                    logger.info("list_page_loaded", extra={"hits_on_first_page": len(hits)})

                # Process hits (filter by window)
                if hits:
                    oldest = min(h.published_at_utc for h in hits)
                else:
                    oldest = None

                for h in hits:
                    if not (date_from <= h.published_at_utc <= date_to):
                        continue

                    ids = make_ids(
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_record_id_raw=h.record_id,
                        detail_url=h.detail_url,
                        download_url=None,
                    )

                    yield DiscoveryRecord(
                        discovery_id=ids.discovery_id,
                        crawl_run_id=crawl_run_id,
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_name=self.source_name,
                        source_record_id_raw=h.record_id,
                        issuer_name_raw=h.issuer_name_raw,
                        issuer_id_raw=None,
                        isin=None,
                        lei=None,
                        filing_type_raw="inside_information",
                        title_raw=h.title_raw,
                        published_at_raw=h.published_at_raw,
                        published_at_utc=h.published_at_utc,
                        period_end_raw=None,
                        period_end_date=None,
                        language_raw="en",
                        detail_url=h.detail_url,
                        download_url=None,
                        metadata_payload_raw={"list_page_url": page_url},
                        discovered_at_utc=now_utc(),
                        discovery_status="DISCOVERED",
                    )

                # Stop rule: list is desc by date; if oldest is older than date_from, stop crawling older pages
                if oldest is not None and oldest < date_from:
                    logger.info("stop_by_date_from", extra={"page_url": page_url, "oldest": oldest.isoformat()})
                    break

                # Pagination
                if next_page and next_page not in seen:
                    queue.append(next_page)
                else:
                    # If we couldn't find next page reliably, we stop after first page.
                    # (Keeps small-window runs correct; for large backfill we can harden pagination later.)
                    if pages_visited == 1:
                        logger.info("no_next_page_found", extra={"page_url": page_url})
                    break

    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        if not discovery.detail_url:
            raise ValueError("Discovery missing detail_url")

        extra = dict(self.config.get("extra", {}) or {})
        base_url = "https://www.afm.nl"

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        timeout_s = int(self.config.get("timeout_s", 60))

        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
            # 1) GET detail (cookies/session) :contentReference[oaicite:7]{index=7}
            await self._throttle()
            d = await client.get(discovery.detail_url)
            d.raise_for_status()

            parsed = parse_detail_html(d.text, base_url=base_url)
            downloads = parsed.get("related_downloads") or []
            if not downloads:
                raise RuntimeError("No related downloads found on detail page")

            # Prefer first download (store hint if multiple)
            dl = downloads[0]
            filename = dl.filename
            download_url = dl.href  # may 404 without referer if hit directly :contentReference[oaicite:8]{index=8}

            # 2) GET file with Referer (important for AFM) :contentReference[oaicite:9]{index=9}
            await self._throttle()
            r = await client.get(download_url, headers={"Referer": discovery.detail_url})
            r.raise_for_status()

            content = r.content
            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower() or None
            cd = r.headers.get("Content-Disposition")

            ext = None
            if filename and "." in filename:
                ext = filename.rsplit(".", 1)[-1].lower().strip()
            if not ext:
                if ct == "application/pdf":
                    ext = "pdf"
                else:
                    ext = "bin"

            # Stable document_id (avoid enc= volatility): tie it to filename
            ids = make_ids(
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                source_record_id_raw=discovery.source_record_id_raw,
                detail_url=discovery.detail_url,
                download_url=f"file:{filename}",
            )

            version_hint = None
            if len(downloads) > 1:
                version_hint = f"multiple_downloads:{len(downloads)}"

            doc = DocumentRecord(
                document_id=ids.document_id,
                discovery_id=discovery.discovery_id,
                crawl_run_id=crawl_run_id,
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                issuer_name_raw=parsed.get("issuer_name_raw") or discovery.issuer_name_raw,
                isin=None,
                lei=None,
                filing_type_raw=discovery.filing_type_raw,
                title_raw=parsed.get("title_raw") or discovery.title_raw,
                published_at_utc=discovery.published_at_utc,
                download_url=download_url,
                final_url=str(r.url),
                http_status=r.status_code,
                mime_type=ct,
                file_extension=ext,
                bytes=None,
                sha256=None,
                storage_key=None,
                downloaded_at_utc=now_utc(),
                download_status="DOWNLOADED",
                version_hint=version_hint,
                content_disposition=cd,
                etag=r.headers.get("ETag"),
                last_modified=r.headers.get("Last-Modified"),
            )
            return doc, content