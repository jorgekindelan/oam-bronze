from __future__ import annotations

import io
import zipfile
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

from oam.connectors.nl.afm_finrep_parsers import (
    extract_isin,
    extract_language,
    extract_lei,
    extract_period_end,
    iter_export_hits,
    parse_detail_html,
)
logger = get_logger("oam.nl.afm_finrep")

DEFAULT_EXPORT_XML_URL = "https://www.afm.nl/export.aspx?format=xml&type=e8825b05-4004-4301-b736-651e8c61053d"
DEFAULT_DETAIL_URL_TEMPLATE = (
    "https://www.afm.nl/en/sector/registers/meldingenregisters/financiele-verslaggeving/details?id={record_id}"
)


@register
class NLAFMFinRep(BaseConnector):
    country_code = "NL"
    source_code = "AFM_FINREP"
    source_name = "AFM Register of financial reporting"

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if state is None:
            return {"watermark_published_at_utc": None}
        state.setdefault("watermark_published_at_utc", None)
        return state

    async def discover(
        self,
        *,
        crawl_run_id: str,
        date_from: datetime,
        date_to: datetime,
        checkpoint: Optional[dict[str, Any]],
    ) -> AsyncIterator[DiscoveryRecord]:
        extra = dict(self.config.get("extra", {}) or {})
        export_url = str(extra.get("export_xml_url") or DEFAULT_EXPORT_XML_URL)
        detail_tpl = str(extra.get("detail_url_template") or DEFAULT_DETAIL_URL_TEMPLATE)
        source_tz = ZoneInfo(str(extra.get("timezone") or "Europe/Amsterdam"))

        # IMPORTANT: use a browser-like UA to avoid export being served as an error HTML.
        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        headers.setdefault("Accept", "text/plain,*/*;q=0.8")

        timeout_s = int(self.config.get("timeout_s", 60))

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            r = await client.get(export_url)
            r.raise_for_status()

            export_text = r.text
            hits = iter_export_hits(export_text, source_tz=source_tz)

            if len(hits) == 0:
                preview = export_text[:400].replace("\n", "\\n")
                raise RuntimeError(
                    f"AFM export parsed 0 hits. status={r.status_code} "
                    f"content_type={r.headers.get('Content-Type')} preview={preview}"
                )

            window_hits = [h for h in hits if date_from <= h.published_at_utc <= date_to]
            logger.info(
                "export_parsed",
                extra={"hits_total": len(hits), "hits_in_window": len(window_hits)},
            )

            watermark: Optional[str] = checkpoint.get("watermark_published_at_utc") if checkpoint else None

            for h in window_hits:
                issuer = h.issuer_name_raw
                filename = h.document_filename_raw
                filing_type = h.filing_type_raw
                reporting_year = h.reporting_year_raw

                lei = extract_lei(filename) or extract_lei(h.raw_chunk)
                isin = extract_isin(filename) or extract_isin(h.raw_chunk)
                lang = extract_language(filename)
                period_end_raw, period_end_date = extract_period_end(filename)

                detail_url = detail_tpl.format(record_id=h.record_id)

                ids = make_ids(
                    country_code=self.country_code,
                    source_code=self.source_code,
                    source_record_id_raw=h.record_id,
                    detail_url=detail_url,
                    download_url=None,
                )

                title = f"{issuer}, {filing_type}" if issuer and filing_type else None

                yield DiscoveryRecord(
                    discovery_id=ids.discovery_id,
                    crawl_run_id=crawl_run_id,
                    country_code=self.country_code,
                    source_code=self.source_code,
                    source_name=self.source_name,
                    source_record_id_raw=h.record_id,
                    issuer_name_raw=issuer,
                    isin=isin,
                    lei=lei,
                    filing_type_raw=filing_type,
                    title_raw=title,
                    published_at_raw=h.published_at_raw,
                    published_at_utc=h.published_at_utc,
                    period_end_raw=period_end_raw,
                    period_end_date=period_end_date,
                    language_raw=lang,
                    detail_url=detail_url,
                    download_url=None,
                    metadata_payload_raw={
                        "export_url": export_url,
                        "export_vermelding_xml": h.raw_chunk,
                        "boekjaar": reporting_year,
                        "objecttype_eng": h.filing_type_eng_raw,
                    },
                    discovered_at_utc=now_utc(),
                    discovery_status="DISCOVERED",
                )

                ts = h.published_at_utc.isoformat()
                if watermark is None or ts > watermark:
                    watermark = ts

            if checkpoint is not None and watermark is not None:
                checkpoint["watermark_published_at_utc"] = watermark

    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        if not discovery.detail_url:
            raise ValueError("Discovery missing detail_url")

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        )
        timeout_s = int(self.config.get("timeout_s", 60))

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            # 1) detail page to resolve download url (and set cookies)
            d = await client.get(discovery.detail_url)
            d.raise_for_status()
            detail = parse_detail_html(d.text)
            download_url = detail.get("download_url")
            if not download_url:
                raise RuntimeError("Could not extract download_url from detail page")

            # 2) download with Referer
            r = await client.get(download_url, headers={"Referer": discovery.detail_url})
            r.raise_for_status()

            content = r.content
            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower() or None
            cd = r.headers.get("Content-Disposition")

            filename = detail.get("document_filename_raw")
            file_extension = None
            if cd and "filename=" in cd:
                part = cd.split("filename=", 1)[1].strip().strip('"').strip("'")
                filename = filename or part
            if filename and "." in filename:
                file_extension = filename.rsplit(".", 1)[-1].lower().strip()

            if not file_extension:
                if ct == "application/pdf":
                    file_extension = "pdf"
                elif ct in ("application/zip", "application/x-zip-compressed"):
                    file_extension = "zip"
                else:
                    file_extension = "bin"

            # Decompress zip: extract the first member and use it instead
            if file_extension == "zip" or ct in ("application/zip", "application/x-zip-compressed"):
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    members = [m for m in zf.namelist() if not m.endswith("/")]
                    if members:
                        inner_name = members[0]
                        content = zf.read(inner_name)
                        file_extension = inner_name.rsplit(".", 1)[-1].lower().strip() if "." in inner_name else "bin"
                        filename = inner_name

            ids = make_ids(
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                source_record_id_raw=discovery.source_record_id_raw,
                detail_url=discovery.detail_url,
                download_url=download_url,
            )

            doc = DocumentRecord(
                document_id=ids.document_id,
                discovery_id=discovery.discovery_id,
                crawl_run_id=crawl_run_id,
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                issuer_name_raw=discovery.issuer_name_raw,
                isin=discovery.isin,
                lei=discovery.lei,
                filing_type_raw=discovery.filing_type_raw,
                title_raw=discovery.title_raw,
                published_at_utc=discovery.published_at_utc,
                download_url=download_url,
                final_url=str(r.url),
                http_status=r.status_code,
                mime_type=ct,
                file_extension=file_extension,
                bytes=None,
                sha256=None,
                storage_key=None,
                downloaded_at_utc=now_utc(),
                download_status="DOWNLOADED",
                content_disposition=cd,
                etag=r.headers.get("ETag"),
                last_modified=r.headers.get("Last-Modified"),
            )
            return doc, content
