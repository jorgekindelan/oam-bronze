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

from oam.connectors.nl.afm_inside_info_parsers import parse_detail_page, parse_list_page

logger = get_logger("oam.nl.afm_inside_info")

DEFAULT_REGISTER_URL = "https://www.afm.nl/en/sector/registers/meldingenregisters/openbaarmaking-voorwetenschap"


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
            "Mozilla/5.0 (oam-bronze; +https://github.com/jorgekindelan/oam-bronze)",
        )
        timeout_s = int(self.config.get("timeout_s", 60))

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            # Seed with most recent detail from first page
            await self._throttle()
            r = await client.get(register_url)
            r.raise_for_status()

            hits = parse_list_page(r.text, base_url=base_url, source_tz=tz)
            if not hits:
                raise RuntimeError("AFM_INSIDE_INFO: could not parse any hits from register page")
            current_detail_url = hits[0].detail_url
            logger.info("seed_detail", extra={"detail_url": current_detail_url, "record_id": hits[0].record_id})

            visited_ids: set[str] = set()
            in_window = False
            watermark: Optional[str] = checkpoint.get("watermark_published_at_utc") if checkpoint else None

            while current_detail_url:
                await self._throttle()
                d = await client.get(current_detail_url)
                d.raise_for_status()

                parsed = parse_detail_page(
                    d.text,
                    detail_url=current_detail_url,
                    base_url=base_url,
                    source_tz=tz,
                )

                if parsed.record_id in visited_ids:
                    logger.info("stop_loop_detected", extra={"record_id": parsed.record_id})
                    break
                visited_ids.add(parsed.record_id)

                if parsed.published_at_utc < date_from:
                    logger.info("stop_by_date_from", extra={"record_id": parsed.record_id})
                    break

                if parsed.published_at_utc <= date_to:
                    in_window = True

                if in_window and (date_from <= parsed.published_at_utc <= date_to):
                    downloads = parsed.downloads
                    if not downloads:
                        # IMPORTANT: no HTML fallback. If a record has no attachment, we skip it.
                        logger.info("no_related_downloads", extra={"record_id": parsed.record_id})
                    else:
                        for idx, dl in enumerate(downloads, start=1):
                            stable_key = f"file:{dl.filename}"

                            ids = make_ids(
                                country_code=self.country_code,
                                source_code=self.source_code,
                                source_record_id_raw=parsed.record_id,
                                detail_url=current_detail_url,
                                download_url=stable_key,  # stable per attachment
                            )

                            yield DiscoveryRecord(
                                discovery_id=ids.discovery_id,
                                crawl_run_id=crawl_run_id,
                                country_code=self.country_code,
                                source_code=self.source_code,
                                source_name=self.source_name,
                                source_record_id_raw=parsed.record_id,
                                issuer_name_raw=parsed.issuer_name_raw,
                                issuer_id_raw=None,
                                isin=None,
                                lei=None,
                                filing_type_raw="inside_information",  # System-assigned constant: AFM does not expose a per-record filing type on this register (type-homogeneous register).
                                title_raw=f"{parsed.title_raw} | {dl.filename}",
                                published_at_raw=parsed.published_at_raw,
                                published_at_utc=parsed.published_at_utc,
                                period_end_raw=None,
                                period_end_date=None,
                                language_raw="en",
                                detail_url=current_detail_url,
                                download_url=stable_key,
                                metadata_payload_raw={
                                    "attachment_filename": dl.filename,
                                    "attachment_index": idx,
                                    "attachments_total": len(downloads),
                                },
                                discovered_at_utc=now_utc(),
                                discovery_status="DISCOVERED",
                            )

                    ts = parsed.published_at_utc.isoformat()
                    if watermark is None or ts > watermark:
                        watermark = ts

                current_detail_url = parsed.older_detail_url

            if checkpoint is not None and watermark is not None:
                checkpoint["watermark_published_at_utc"] = watermark

    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        if not discovery.detail_url:
            raise ValueError("Discovery missing detail_url")

        tz = ZoneInfo("Europe/Amsterdam")
        base_url = "https://www.afm.nl"

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (oam-bronze; +https://github.com/jorgekindelan/oam-bronze)",
        )
        timeout_s = int(self.config.get("timeout_s", 60))

        wanted = (discovery.metadata_payload_raw or {}).get("attachment_filename")

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            # Always re-parse detail NOW to get a valid enc= link.
            await self._throttle()
            d = await client.get(discovery.detail_url)
            d.raise_for_status()

            parsed = parse_detail_page(d.text, detail_url=discovery.detail_url, base_url=base_url, source_tz=tz)
            downloads = parsed.downloads
            if not downloads:
                raise RuntimeError("No related downloads found on detail page (cannot download attachment)")

            # pick by filename match, else first
            chosen = None
            if wanted:
                for dl in downloads:
                    if dl.filename.strip() == str(wanted).strip():
                        chosen = dl
                        break
            if chosen is None:
                chosen = downloads[0]

            filename = chosen.filename
            href = chosen.href

            ext = filename.rsplit(".", 1)[-1].lower().strip() if "." in filename else "bin"

            # Download with Referer; direct access can 404 without context. :contentReference[oaicite:3]{index=3}
            await self._throttle()
            r = await client.get(href, headers={"Referer": discovery.detail_url})
            if r.status_code >= 400:
                # retry after visiting detail again
                await self._throttle()
                _ = await client.get(discovery.detail_url)
                await self._throttle()
                r = await client.get(href, headers={"Referer": discovery.detail_url})
            r.raise_for_status()

            content = r.content
            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower() or None

            # stable document_id: use discovery.download_url = file:<filename>
            ids = make_ids(
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                source_record_id_raw=discovery.source_record_id_raw,
                detail_url=discovery.detail_url,
                download_url=discovery.download_url or f"file:{filename}",
            )

            doc = DocumentRecord(
                document_id=ids.document_id,
                discovery_id=discovery.discovery_id,
                crawl_run_id=crawl_run_id,
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                issuer_name_raw=discovery.issuer_name_raw,
                isin=None,
                lei=None,
                filing_type_raw=discovery.filing_type_raw,
                title_raw=discovery.title_raw,
                published_at_utc=discovery.published_at_utc,
                download_url=href,
                final_url=str(r.url),
                http_status=r.status_code,
                mime_type=ct,
                file_extension=ext,
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
