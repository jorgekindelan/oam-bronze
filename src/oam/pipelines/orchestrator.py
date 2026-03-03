from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from oam.connectors.base import BaseConnector
from oam.core.hashing import sha256_bytes
from oam.core.logging import get_logger
from oam.core.time import now_utc
from oam.models.discovery import DiscoveryRecord
from oam.models.document import DocumentRecord
from oam.models.run import RunReport
from oam.persistence.db import SQLiteMetadataStore
from oam.persistence.filesystem_store import FilesystemStore, build_binary_storage_key
from oam.persistence.raw_payload_store import RawPayloadStore


logger = get_logger("oam")


def _parse_dt(v: Optional[str]) -> Optional[datetime]:
    if v is None:
        return None
    return datetime.fromisoformat(v)


def _row_to_discovery(row: Any) -> DiscoveryRecord:
    return DiscoveryRecord(
        discovery_id=row["discovery_id"],
        crawl_run_id=row["crawl_run_id"],
        country_code=row["country_code"],
        source_code=row["source_code"],
        source_name=row["source_name"],
        source_record_id_raw=row["source_record_id_raw"],
        issuer_name_raw=row["issuer_name_raw"],
        issuer_id_raw=row["issuer_id_raw"],
        isin=row["isin"],
        lei=row["lei"],
        filing_type_raw=row["filing_type_raw"],
        title_raw=row["title_raw"],
        published_at_raw=row["published_at_raw"],
        published_at_utc=_parse_dt(row["published_at_utc"]),
        period_end_raw=row["period_end_raw"],
        period_end_date=row["period_end_date"],
        language_raw=row["language_raw"],
        detail_url=row["detail_url"],
        download_url=row["download_url"],
        metadata_payload_raw=None,  # we keep raw in storage; DB has json string anyway
        discovered_at_utc=_parse_dt(row["discovered_at_utc"]),
        discovery_status=row["discovery_status"],
    )


@dataclass(frozen=True)
class RuntimePaths:
    root: Path

    def raw_discovery_key(self, *, country_code: str, source_code: str, crawl_run_id: str, discovery_id: str) -> str:
        return f"raw_discovery/{country_code}/{source_code}/{crawl_run_id}/{discovery_id}.json"

    def snapshot_key(self, *, country_code: str, source_code: str, day: str, crawl_run_id: str) -> str:
        return f"snapshots/{country_code}/{source_code}/{day}/{crawl_run_id}.jsonl"

    def report_key(self, *, country_code: str, source_code: str, crawl_run_id: str) -> str:
        return f"run_reports/{country_code}/{source_code}/{crawl_run_id}/report.json"


async def run_discovery(
    *,
    db: SQLiteMetadataStore,
    raw_store: RawPayloadStore,
    connector: BaseConnector,
    crawl_run_id: str,
    date_from: datetime,
    date_to: datetime,
    paths: RuntimePaths,
    report: RunReport,
) -> None:
    checkpoint = db.load_checkpoint(country_code=connector.country_code, source_code=connector.source_code)
    checkpoint = connector.checkpoint_load(checkpoint)

    day = now_utc().date().isoformat()
    snapshot_key = paths.snapshot_key(
        country_code=connector.country_code,
        source_code=connector.source_code,
        day=day,
        crawl_run_id=crawl_run_id,
    )
    snapshot_path = paths.root / snapshot_key
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    with snapshot_path.open("a", encoding="utf-8") as snap:
        try:
            async for rec in connector.discover(
                crawl_run_id=crawl_run_id,
                date_from=date_from,
                date_to=date_to,
                checkpoint=checkpoint,
            ):
                raw_key = paths.raw_discovery_key(
                    country_code=rec.country_code,
                    source_code=rec.source_code,
                    crawl_run_id=crawl_run_id,
                    discovery_id=rec.discovery_id,
                )

                # Store raw payload (probatory)
                if rec.metadata_payload_raw is not None:
                    raw_store.write_json(raw_key, rec.metadata_payload_raw)

                inserted = db.insert_discovery(rec)
                if inserted:
                    report.discovered_count += 1
                    db.insert_event(
                        crawl_run_id=crawl_run_id,
                        stage="discover",
                        status="ok",
                        event_at_utc=now_utc(),
                        discovery_id=rec.discovery_id,
                    )
                else:
                    db.insert_event(
                        crawl_run_id=crawl_run_id,
                        stage="discover",
                        status="duplicate",
                        event_at_utc=now_utc(),
                        discovery_id=rec.discovery_id,
                    )

                line = rec.model_dump()
                line["raw_payload_key"] = raw_key
                snap.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")

        except Exception as e:
            report.discovery_error_count += 1
            db.insert_event(
                crawl_run_id=crawl_run_id,
                stage="discover",
                status="error",
                event_at_utc=now_utc(),
                error_code="DISCOVERY_EXCEPTION",
                error_message=str(e),
            )
            raise

    # Save checkpoint (connector-defined)
    if checkpoint is not None:
        db.upsert_checkpoint(
            country_code=connector.country_code,
            source_code=connector.source_code,
            state=connector.checkpoint_save(checkpoint),
            updated_at_utc=now_utc(),
        )


async def run_download(
    *,
    db: SQLiteMetadataStore,
    obj_store: FilesystemStore,
    connector: BaseConnector,
    crawl_run_id: str,
    paths: RuntimePaths,
    report: RunReport,
    limit: int = 1000,
) -> None:
    pending_rows = db.get_pending_downloads(
        country_code=connector.country_code,
        source_code=connector.source_code,
        limit=limit,
    )
    report.download_candidate_count = len(pending_rows)

    for row in pending_rows:
        discovery = _row_to_discovery(row)
        try:
            doc_rec, content = await connector.download_document(crawl_run_id=crawl_run_id, discovery=discovery)

            # Hash
            sha = sha256_bytes(content)
            doc_rec.sha256 = sha
            doc_rec.bytes = len(content)

            # Cross-document dedup by sha
            existing_sha = db.find_by_sha256(sha256=sha)
            if existing_sha is not None:
                doc_rec.download_status = "DUPLICATE_SHA"
                doc_rec.storage_key = existing_sha["storage_key"]
                doc_rec.version_hint = f"dedup_ref:{existing_sha['document_id']}@v{existing_sha['version_no']}"
                report.duplicate_sha_count += 1

            # Versioning per document_id
            latest = db.get_latest_document_version(document_id=doc_rec.document_id)
            if latest is None:
                doc_rec.version_no = 1
            else:
                latest_sha = latest["sha256"]
                if latest_sha == sha:
                    # Same logical doc, same content: idempotent
                    doc_rec.version_no = int(latest["version_no"])
                    doc_rec.download_status = "DUPLICATE_SHA"
                    doc_rec.storage_key = latest["storage_key"]
                    report.duplicate_sha_count += 1
                else:
                    doc_rec.version_no = int(latest["version_no"]) + 1
                    if doc_rec.version_hint is None:
                        doc_rec.version_hint = "content_changed"

            # Persist bytes (only if not a duplicate referencing existing storage)
            if not doc_rec.storage_key:
                doc_rec.storage_key = build_binary_storage_key(
                    country_code=doc_rec.country_code,
                    source_code=doc_rec.source_code,
                    published_at_utc=doc_rec.published_at_utc,
                    issuer_name_raw=doc_rec.issuer_name_raw,
                    document_id=doc_rec.document_id,
                    file_extension=doc_rec.file_extension,
                )
                obj_store.put_bytes(doc_rec.storage_key, content, overwrite=False)
                report.bytes_downloaded += len(content)

            inserted = db.insert_document_version(doc_rec)
            if inserted and doc_rec.download_status == "DOWNLOADED":
                report.downloaded_count += 1
            elif doc_rec.download_status == "DUPLICATE_SHA":
                # we count duplicates as successful downloads from a coverage perspective
                pass

            db.insert_event(
                crawl_run_id=crawl_run_id,
                stage="download",
                status="ok" if doc_rec.download_status in ("DOWNLOADED", "DUPLICATE_SHA") else "error",
                event_at_utc=now_utc(),
                discovery_id=doc_rec.discovery_id,
                document_id=doc_rec.document_id,
                version_no=doc_rec.version_no,
                http_status=doc_rec.http_status,
                bytes_=doc_rec.bytes,
                sha256=doc_rec.sha256,
                error_code=doc_rec.error_code,
                error_message=doc_rec.error_message,
            )

        except Exception as e:
            report.download_error_count += 1
            db.insert_event(
                crawl_run_id=crawl_run_id,
                stage="download",
                status="error",
                event_at_utc=now_utc(),
                discovery_id=discovery.discovery_id,
                error_code="DOWNLOAD_EXCEPTION",
                error_message=str(e),
            )


def write_report(*, paths: RuntimePaths, report: RunReport) -> None:
    report.finished_at_utc = now_utc()
    key = paths.report_key(
        country_code=report.country_code,
        source_code=report.source_code,
        crawl_run_id=report.crawl_run_id,
    )
    path = paths.root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.model_dump(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
