from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator, Optional
from zoneinfo import ZoneInfo
import asyncio

import httpx

from oam.connectors.base import BaseConnector
from oam.connectors.registry import register
from oam.core.ids import make_ids
from oam.core.logging import get_logger
from oam.core.time import now_utc
from oam.models.discovery import DiscoveryRecord
from oam.models.document import DocumentRecord

from oam.connectors.nl_afm_issued_capital_parsers import (
    parse_list_page,
    parse_detail_page,
    parse_ui_date_dd_mmm_yyyy,
)

logger = get_logger("oam.nl.afm_issued_capital")


@register
class NLAFMIssuedCapital(BaseConnector):
    country_code = "NL"
    source_code = "AFM_ISSUED_CAPITAL"
    source_name = "AFM Register issued capital (geplaatst kapitaal)"

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if state is None:
            return {"watermark_published_at_utc": None}
        state.setdefault("watermark_published_at_utc", None)
        return state

    async def _throttle(self) -> None:
        rps = float(self.config.get("throttle_rps", 2.0))
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
        register_url = str(extra["register_url"])
        base_url = "https://www.afm.nl"
        tz = ZoneInfo(str(extra.get("timezone", "Europe/Amsterdam")))

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault("User-Agent", "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)")
        timeout_s = int(self.config.get("timeout_s", 60))

        seen_pages: set[str] = set()
        queue: list[str] = [register_url]

        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
            while queue:
                page_url = queue.pop(0)
                if page_url in seen_pages:
                    continue
                seen_pages.add(page_url)

                await self._throttle()
                r = await client.get(page_url)
                r.raise_for_status()

                rows, page_urls = parse_list_page(r.text, base_url=base_url, tz=tz)
                # Encolar páginas descubiertas (paginación)
                for u in page_urls:
                    if u not in seen_pages:
                        queue.append(u)

                # Procesar filas
                for row in rows:
                    if not (date_from <= row.date_utc <= date_to):
                        continue

                    ids = make_ids(
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_record_id_raw=row.record_id,
                        detail_url=row.detail_url,
                        download_url=None,
                    )

                    yield DiscoveryRecord(
                        discovery_id=ids.discovery_id,
                        crawl_run_id=crawl_run_id,
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_name=self.source_name,
                        source_record_id_raw=row.record_id,
                        issuer_name_raw=row.issuer_name_raw,
                        issuer_id_raw=None,  # kvk viene en detail
                        isin=None,  # ISINs se extraen en download
                        lei=None,
                        filing_type_raw="issued_capital",
                        title_raw=f"{row.issuer_name_raw} - issued capital",
                        published_at_raw=row.date_raw,
                        published_at_utc=row.date_utc,
                        period_end_raw=None,
                        period_end_date=None,
                        language_raw="en",
                        detail_url=row.detail_url,
                        download_url=None,
                        metadata_payload_raw={
                            "place_raw": row.place_raw,
                            "list_page_url": page_url,
                        },
                        discovered_at_utc=now_utc(),
                        discovery_status="DISCOVERED",
                    )

    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        if not discovery.detail_url:
            raise ValueError("Discovery missing detail_url")

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault("User-Agent", "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)")
        timeout_s = int(self.config.get("timeout_s", 60))

        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True, headers=headers) as client:
            await self._throttle()
            r = await client.get(discovery.detail_url)
            r.raise_for_status()
            html_bytes = r.content

            parsed = parse_detail_page(r.text)

            ids = make_ids(
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                source_record_id_raw=discovery.source_record_id_raw,
                detail_url=discovery.detail_url,
                download_url=None,
            )

            doc = DocumentRecord(
                document_id=ids.document_id,
                discovery_id=discovery.discovery_id,
                crawl_run_id=crawl_run_id,
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                issuer_name_raw=parsed.get("issuer_name_raw") or discovery.issuer_name_raw,
                isin=",".join(parsed.get("isins") or []) or None,  # bronce: puedes dejarlo raw string
                lei=None,
                filing_type_raw=discovery.filing_type_raw,
                title_raw=discovery.title_raw,
                published_at_utc=discovery.published_at_utc,
                download_url=None,
                final_url=str(r.url),
                http_status=r.status_code,
                mime_type="text/html",
                file_extension="html",
                bytes=None,
                sha256=None,
                storage_key=None,
                downloaded_at_utc=now_utc(),
                download_status="DOWNLOADED",
                version_hint=None,
                content_disposition=None,
                etag=r.headers.get("ETag"),
                last_modified=r.headers.get("Last-Modified"),
                error_code=None,
                error_message=None,
            )

            # importante: preservamos el HTML crudo; parsed va en metadata del discovery 
            return doc, html_bytes