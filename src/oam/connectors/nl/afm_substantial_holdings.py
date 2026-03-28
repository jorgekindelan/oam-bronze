from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from zoneinfo import ZoneInfo

from oam.connectors.base import BaseConnector
from oam.connectors.registry import register
from oam.core.http import build_async_client
from oam.core.ids import make_ids
from oam.core.logging import get_logger
from oam.core.time import now_utc
from oam.models.discovery import DiscoveryRecord
from oam.models.document import DocumentRecord

from oam.connectors.nl.afm_substantial_holdings_parsers import (
    parse_detail_page,
    parse_list_page,
)

logger = get_logger("oam.nl.afm_substantial_holdings")


@register
class NLAFMSubstantialHoldings(BaseConnector):
    country_code = "NL"
    source_code = "AFM_SUBSTANTIAL_HOLDINGS"
    source_name = "AFM Register substantial holdings and gross short positions"

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
        register_url = extra.get("register_url")
        if not register_url:
            raise ValueError("AFM_SUBSTANTIAL_HOLDINGS requires 'register_url' in extra config")
        register_url = str(register_url)
        tz = ZoneInfo(str(extra.get("timezone") or "Europe/Amsterdam"))
        base_url = "https://www.afm.nl"

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault("User-Agent", "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)")
        timeout_s = int(self.config.get("timeout_s", 60))

        seen_pages: set[str] = set()
        page_url: Optional[str] = register_url
        pages_seen = 0

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            while page_url:
                if page_url in seen_pages:
                    break
                seen_pages.add(page_url)
                pages_seen += 1

                await self._throttle()
                r = await client.get(page_url)
                r.raise_for_status()

                hits, next_page_url = parse_list_page(
                    r.text,
                    page_url=page_url,
                    base_url=base_url,
                    source_tz=tz,
                    register_path_contains="/substantiele-deelnemingen",
                )

                if pages_seen == 1:
                    logger.info("list_page_loaded", extra={"hits_on_first_page": len(hits)})

                if not hits:
                    logger.info("empty_list_page", extra={"page_url": page_url})
                    break

                oldest_on_page = min(h.published_at_utc for h in hits)

                for h in hits:
                    if not (date_from <= h.published_at_utc <= date_to):
                        continue

                    ids = make_ids(
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_record_id_raw=h.record_id,
                        detail_url=h.detail_url,
                        download_url="file:detail.html",
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
                        filing_type_raw="substantial_holdings_and_gross_short_positions",
                        title_raw=h.notifier_raw,
                        published_at_raw=h.published_at_raw,
                        published_at_utc=h.published_at_utc,
                        period_end_raw=None,
                        period_end_date=None,
                        language_raw="en",
                        detail_url=h.detail_url,
                        download_url="file:detail.html",
                        metadata_payload_raw={
                            "notifier_raw": h.notifier_raw,
                            "list_page_url": page_url,
                        },
                        discovered_at_utc=now_utc(),
                        discovery_status="DISCOVERED",
                    )

                if oldest_on_page < date_from:
                    logger.info("stop_by_date_from", extra={"page_url": page_url, "oldest": oldest_on_page.isoformat()})
                    break

                page_url = next_page_url

    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        if not discovery.detail_url:
            raise ValueError("Discovery missing detail_url")

        tz = ZoneInfo("Europe/Amsterdam")
        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault("User-Agent", "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)")
        timeout_s = int(self.config.get("timeout_s", 60))

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            await self._throttle()
            r = await client.get(discovery.detail_url)
            r.raise_for_status()

            parsed = parse_detail_page(r.text, detail_url=discovery.detail_url, source_tz=tz)
            content = r.content
            ct = (r.headers.get("Content-Type") or "text/html").split(";")[0].strip().lower()

            ids = make_ids(
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                source_record_id_raw=discovery.source_record_id_raw,
                detail_url=discovery.detail_url,
                download_url=discovery.download_url or "file:detail.html",
            )

            doc = DocumentRecord(
                document_id=ids.document_id,
                discovery_id=discovery.discovery_id,
                crawl_run_id=crawl_run_id,
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                issuer_name_raw=parsed.issuer_name_raw or discovery.issuer_name_raw,
                isin=",".join(parsed.isins) if parsed.isins else None,
                lei=None,
                filing_type_raw=discovery.filing_type_raw,
                title_raw=parsed.notifier_raw or discovery.title_raw,
                published_at_utc=parsed.published_at_utc or discovery.published_at_utc,
                download_url=None,
                final_url=str(r.url),
                http_status=r.status_code,
                mime_type=ct,
                file_extension="html",
                bytes=None,
                sha256=None,
                storage_key=None,
                downloaded_at_utc=now_utc(),
                download_status="DOWNLOADED",
                version_hint=None,
                content_disposition=r.headers.get("Content-Disposition"),
                etag=r.headers.get("ETag"),
                last_modified=r.headers.get("Last-Modified"),
                error_code=None,
                error_message=None,
            )
            return doc, content
