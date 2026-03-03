from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, AsyncIterator, Optional

from oam.connectors.base import BaseConnector
from oam.connectors.registry import register
from oam.core.ids import make_ids
from oam.core.time import now_utc
from oam.models.discovery import DiscoveryRecord
from oam.models.document import DocumentRecord


_DUMMY_PDF = (
    b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>\nendobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000015 00000 n \n0000000065 00000 n \n0000000122 00000 n \n"
    b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n180\n%%EOF\n"
)


@register
class DummyConnector(BaseConnector):
    country_code = "ZZ"
    source_code = "DUMMY"
    source_name = "Dummy Source"

    async def discover(
        self,
        *,
        crawl_run_id: str,
        date_from: datetime,
        date_to: datetime,
        checkpoint: Optional[dict[str, Any]],
    ) -> AsyncIterator[DiscoveryRecord]:
        # Emit 2 deterministic records within the requested window.
        base_dt = datetime(2015, 1, 1, 12, 0, tzinfo=UTC)
        candidates = [
            {
                "source_record_id_raw": "1",
                "issuer_name_raw": "ACME NV",
                "filing_type_raw": "Annual report",
                "title_raw": "ACME Annual report 2014",
                "published_at_utc": base_dt,
                "detail_url": "https://example.invalid/detail/1",
                "download_url": "https://example.invalid/download/1.pdf",
            },
            {
                "source_record_id_raw": "2",
                "issuer_name_raw": "FOO SA",
                "filing_type_raw": "Half-year report",
                "title_raw": "FOO Half-year 2014",
                "published_at_utc": base_dt.replace(day=2),
                "detail_url": "https://example.invalid/detail/2",
                "download_url": "https://example.invalid/download/2.pdf",
            },
        ]

        for raw in candidates:
            pub = raw["published_at_utc"]
            if pub < date_from or pub > date_to:
                continue

            ids = make_ids(
                country_code=self.country_code,
                source_code=self.source_code,
                source_record_id_raw=raw["source_record_id_raw"],
                detail_url=raw["detail_url"],
                download_url=raw["download_url"],
            )

            yield DiscoveryRecord(
                discovery_id=ids.discovery_id,
                crawl_run_id=crawl_run_id,
                country_code=self.country_code,
                source_code=self.source_code,
                source_name=self.source_name,
                source_record_id_raw=raw["source_record_id_raw"],
                issuer_name_raw=raw["issuer_name_raw"],
                filing_type_raw=raw["filing_type_raw"],
                title_raw=raw["title_raw"],
                published_at_raw=pub.isoformat(),
                published_at_utc=pub,
                detail_url=raw["detail_url"],
                download_url=raw["download_url"],
                metadata_payload_raw={"dummy": True, "record": raw},
                discovered_at_utc=now_utc(),
                discovery_status="DISCOVERED",
            )

    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        ids = make_ids(
            country_code=discovery.country_code,
            source_code=discovery.source_code,
            source_record_id_raw=discovery.source_record_id_raw,
            detail_url=discovery.detail_url,
            download_url=discovery.download_url,
        )
        rec = DocumentRecord(
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
            final_url=discovery.download_url,
            http_status=200,
            mime_type="application/pdf",
            file_extension="pdf",
            bytes=len(_DUMMY_PDF),
            sha256=None,  # filled by pipeline
            storage_key=None,  # filled by pipeline
            downloaded_at_utc=now_utc(),
            download_status="DOWNLOADED",
        )
        return rec, _DUMMY_PDF
