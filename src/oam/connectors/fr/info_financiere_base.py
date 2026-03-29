from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Shared base for all FR info-financiere.gouv.fr connectors.
# All FR buckets use the same OpenDataSoft API endpoint with different
# where_clause filters. This base class handles pagination, checkpointing,
# and download. Subclasses override source_code, source_name, where_clause,
# and optionally _map_hit_to_discovery_fields() for bucket-specific mapping.
# ─────────────────────────────────────────────────────────────────────────────

import io
import zipfile
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Optional

from oam.connectors.base import BaseConnector
from oam.core.http import build_async_client
from oam.core.ids import make_ids
from oam.core.logging import get_logger
from oam.core.time import now_utc
from oam.models.discovery import DiscoveryRecord
from oam.models.document import DocumentRecord

from oam.connectors.fr.info_financiere_parsers import (
    extract_isin,
    extract_issuer_id,
    extract_language,
    extract_period_end,
    parse_records,
)

logger = get_logger("oam.fr.info_financiere_base")

DEFAULT_API_BASE = (
    "https://www.info-financiere.gouv.fr/api/explore/v2.1"
    "/catalog/datasets/flux-amf-new-prod/records"
)
DEFAULT_PAGE_LIMIT = 100


class FRInfoFinanciereBaseConnector(BaseConnector):
    """
    Base connector for all FR info-financiere.gouv.fr buckets.

    Subclasses must set:
      - country_code = "FR"
      - source_code  (e.g. "INFOFIN_HALFREP")
      - source_name
      - default_where_clause  (ODSQL filter string)
    """

    country_code = "FR"
    default_where_clause: str = ""  # must be overridden
    # Subclasses set this to guard against API returning wrong-bucket records.
    # Empty tuple = no guard (base class default). Non-empty = filter enforced.
    expected_filing_types: tuple[str, ...] = ()

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if state is None:
            return {"watermark_uin_dat_amf": None}
        state.setdefault("watermark_uin_dat_amf", None)
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
        api_base = str(extra.get("api_base_url") or DEFAULT_API_BASE)
        where_base = str(extra.get("where_clause") or self.default_where_clause)
        page_limit = int(extra.get("page_limit") or DEFAULT_PAGE_LIMIT)
        order_by = str(extra.get("order_by") or "uin_dat_amf ASC")
        timeout_s = int(self.config.get("timeout_s", 60))

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault("User-Agent", "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)")
        headers.setdefault("Accept", "application/json")

        date_from_str = date_from.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        date_to_str = date_to.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        where = (
            f'{where_base} AND uin_dat_amf >= "{date_from_str}"'
            f' AND uin_dat_amf <= "{date_to_str}"'
        )

        watermark: Optional[str] = checkpoint.get("watermark_uin_dat_amf") if checkpoint else None
        total_yielded = 0
        offset = 0

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            while True:
                params = {
                    "where": where,
                    "order_by": order_by,
                    "limit": page_limit,
                    "offset": offset,
                }
                r = await client.get(api_base, params=params)
                r.raise_for_status()

                try:
                    response_json = r.json()
                except Exception as exc:
                    preview = r.text[:400].replace("\n", "\\n")
                    raise RuntimeError(
                        f"INFOFIN API JSON decode failed ({self.source_code}): {exc} — preview={preview}"
                    ) from exc

                hits = parse_records(response_json)

                if not hits:
                    if offset == 0:
                        logger.info("infofin_discover_empty_window", extra={
                            "source_code": self.source_code,
                            "date_from": date_from_str,
                            "date_to": date_to_str,
                        })
                    break

                for h in hits:
                    if self.expected_filing_types and h.filing_type_raw not in self.expected_filing_types:
                        logger.error(
                            "infofin_unexpected_filing_type",
                            extra={
                                "source_code": self.source_code,
                                "uin_idt_uin": h.uin_idt_uin,
                                "filing_type_raw": h.filing_type_raw,
                                "expected": self.expected_filing_types,
                            },
                        )
                        continue

                    isin = extract_isin(h.isin_raw)
                    lei = h.lei_raw or None
                    issuer_id = extract_issuer_id(h.isin_raw, h.lei_raw)
                    lang = extract_language(h.language_raw_api)
                    period_end_raw, period_end_date = extract_period_end(h.title_raw)

                    if not h.download_url:
                        logger.warning("infofin_missing_download_url", extra={
                            "source_code": self.source_code,
                            "uin_idt_uin": h.uin_idt_uin,
                        })

                    ids = make_ids(
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_record_id_raw=h.uin_idt_uin,
                        detail_url=None,
                        download_url=h.download_url,
                    )

                    status = "DISCOVERED" if h.download_url else "DISCOVERED_NO_DOWNLOAD_URL"

                    yield DiscoveryRecord(
                        discovery_id=ids.discovery_id,
                        crawl_run_id=crawl_run_id,
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_name=self.source_name,
                        source_record_id_raw=h.uin_idt_uin,
                        issuer_name_raw=h.issuer_name_raw,
                        issuer_id_raw=issuer_id,
                        isin=isin,
                        lei=lei,
                        filing_type_raw=h.filing_type_raw,
                        title_raw=h.title_raw,
                        published_at_raw=h.published_at_raw,
                        published_at_utc=h.published_at_utc,
                        period_end_raw=period_end_raw,
                        period_end_date=period_end_date,
                        language_raw=lang,
                        detail_url=None,
                        download_url=h.download_url,
                        metadata_payload_raw={
                            "api_record": h.api_record_raw,
                            "fichier_nom": h.fichier_nom_raw,
                        },
                        discovered_at_utc=now_utc(),
                        discovery_status=status,
                    )

                    total_yielded += 1
                    ts = h.published_at_utc.isoformat()
                    if watermark is None or ts > watermark:
                        watermark = ts

                logger.info("infofin_page_fetched", extra={
                    "source_code": self.source_code,
                    "offset": offset,
                    "hits": len(hits),
                    "total_yielded": total_yielded,
                })

                if len(hits) < page_limit:
                    break
                offset += page_limit

        if checkpoint is not None and watermark is not None:
            checkpoint["watermark_uin_dat_amf"] = watermark

        logger.info("infofin_discover_done", extra={
            "source_code": self.source_code,
            "total_yielded": total_yielded,
        })

    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        if not discovery.download_url:
            raise ValueError(
                f"Discovery {discovery.discovery_id} missing download_url; "
                "cannot download without url_de_recuperation"
            )

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault("User-Agent", "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)")
        headers.setdefault("Accept", "application/pdf,*/*")
        timeout_s = int(self.config.get("timeout_s", 60))

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            r = await client.get(discovery.download_url)
            r.raise_for_status()

            content = r.content
            if not content:
                raise RuntimeError(f"Empty response body for {discovery.download_url}")

            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower() or None
            cd = r.headers.get("Content-Disposition")

            filename = None
            file_extension = None
            if cd and "filename=" in cd:
                part = cd.split("filename=", 1)[1].strip().strip('"').strip("'")
                filename = part
            if not filename:
                url_path = str(r.url).split("?")[0].rstrip("/")
                filename = url_path.rsplit("/", 1)[-1] or None
            if filename and "." in filename:
                file_extension = filename.rsplit(".", 1)[-1].lower().strip()

            if not file_extension:
                if ct == "application/pdf":
                    file_extension = "pdf"
                elif ct in ("application/zip", "application/x-zip-compressed"):
                    file_extension = "zip"
                else:
                    file_extension = "bin"

            # Decompress zip: extract first member (same as NL / B1 pattern)
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
                detail_url=None,
                download_url=discovery.download_url,
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
                download_url=discovery.download_url,
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
