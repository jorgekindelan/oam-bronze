from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from oam.core.ids import uuid7
from oam.models.discovery import DiscoveryRecord
from oam.models.document import DocumentRecord


def _dt_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


@dataclass
class SQLiteMetadataStore:
    db_path: Path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_schema(self) -> None:
        schema_path = Path(__file__).with_name("schema.sql")
        sql = schema_path.read_text(encoding="utf-8")
        with self.connect() as conn:
            conn.executescript(sql)

    def record_crawl_run_start(
        self,
        *,
        crawl_run_id: str,
        started_at_utc: datetime,
        git_sha: Optional[str] = None,
        config_hash: Optional[str] = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO crawl_runs(crawl_run_id, started_at_utc, git_sha, config_hash) VALUES (?, ?, ?, ?)",
                (crawl_run_id, started_at_utc.isoformat(), git_sha, config_hash),
            )

    def record_crawl_run_finish(self, *, crawl_run_id: str, finished_at_utc: datetime) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE crawl_runs SET finished_at_utc = ? WHERE crawl_run_id = ?",
                (finished_at_utc.isoformat(), crawl_run_id),
            )

    def insert_discovery(self, rec: DiscoveryRecord) -> bool:
        payload_json = None
        if rec.metadata_payload_raw is not None:
            payload_json = json.dumps(rec.metadata_payload_raw, ensure_ascii=False, default=str)

        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO discovery_records(
                  discovery_id, crawl_run_id, country_code, source_code, source_name,
                  source_record_id_raw, issuer_name_raw, issuer_id_raw, isin, lei,
                  filing_type_raw, title_raw,
                  published_at_raw, published_at_utc,
                  period_end_raw, period_end_date,
                  language_raw, detail_url, download_url,
                  metadata_payload_raw,
                  discovered_at_utc, discovery_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.discovery_id,
                    rec.crawl_run_id,
                    rec.country_code,
                    rec.source_code,
                    rec.source_name,
                    rec.source_record_id_raw,
                    rec.issuer_name_raw,
                    rec.issuer_id_raw,
                    rec.isin,
                    rec.lei,
                    rec.filing_type_raw,
                    rec.title_raw,
                    rec.published_at_raw,
                    _dt_iso(rec.published_at_utc),
                    rec.period_end_raw,
                    rec.period_end_date,
                    rec.language_raw,
                    rec.detail_url,
                    rec.download_url,
                    payload_json,
                    rec.discovered_at_utc.isoformat(),
                    rec.discovery_status,
                ),
            )
            return cur.rowcount == 1

    def get_pending_downloads(
        self,
        *,
        country_code: str,
        source_code: str,
        limit: int = 1000,
    ) -> list[sqlite3.Row]:
        """Return discovery records that do not have any successful document version."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT d.*
                FROM discovery_records d
                LEFT JOIN document_records r
                  ON r.discovery_id = d.discovery_id
                  AND r.download_status IN ('DOWNLOADED','DUPLICATE_SHA')
                WHERE d.country_code = ?
                  AND d.source_code = ?
                  AND d.discovery_status = 'DISCOVERED'
                  AND r.document_id IS NULL
                ORDER BY d.published_at_utc ASC
                LIMIT ?
                """,
                (country_code, source_code, limit),
            )
            return list(cur.fetchall())

    def get_latest_document_version(self, *, document_id: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM document_records
                WHERE document_id = ?
                ORDER BY version_no DESC
                LIMIT 1
                """,
                (document_id,),
            )
            return cur.fetchone()

    def find_by_sha256(self, *, sha256: str) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM document_records
                WHERE sha256 = ? AND download_status IN ('DOWNLOADED','DUPLICATE_SHA')
                ORDER BY downloaded_at_utc ASC
                LIMIT 1
                """,
                (sha256,),
            )
            return cur.fetchone()

    def insert_document_version(self, rec: DocumentRecord) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO document_records(
                  document_id, version_no,
                  discovery_id, crawl_run_id, country_code, source_code,
                  issuer_name_raw, isin, lei,
                  filing_type_raw, title_raw, published_at_utc,
                  download_url, final_url,
                  http_status, mime_type, file_extension,
                  bytes, sha256, storage_key,
                  downloaded_at_utc, download_status,
                  error_code, error_message, version_hint,
                  content_disposition, etag, last_modified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.document_id,
                    rec.version_no,
                    rec.discovery_id,
                    rec.crawl_run_id,
                    rec.country_code,
                    rec.source_code,
                    rec.issuer_name_raw,
                    rec.isin,
                    rec.lei,
                    rec.filing_type_raw,
                    rec.title_raw,
                    _dt_iso(rec.published_at_utc),
                    rec.download_url,
                    rec.final_url,
                    rec.http_status,
                    rec.mime_type,
                    rec.file_extension,
                    rec.bytes,
                    rec.sha256,
                    rec.storage_key,
                    rec.downloaded_at_utc.isoformat(),
                    rec.download_status,
                    rec.error_code,
                    rec.error_message,
                    rec.version_hint,
                    rec.content_disposition,
                    rec.etag,
                    rec.last_modified,
                ),
            )
            return cur.rowcount == 1

    def insert_event(
        self,
        *,
        crawl_run_id: str,
        stage: str,
        status: str,
        event_at_utc: datetime,
        discovery_id: Optional[str] = None,
        document_id: Optional[str] = None,
        version_no: Optional[int] = None,
        http_status: Optional[int] = None,
        bytes_: Optional[int] = None,
        sha256: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        event_id = str(uuid7())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO download_events(
                  event_id, crawl_run_id, discovery_id, document_id, version_no,
                  event_at_utc, stage, status, http_status, bytes, sha256, error_code, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    crawl_run_id,
                    discovery_id,
                    document_id,
                    version_no,
                    event_at_utc.isoformat(),
                    stage,
                    status,
                    http_status,
                    bytes_,
                    sha256,
                    error_code,
                    error_message,
                ),
            )

    def upsert_checkpoint(
        self,
        *,
        country_code: str,
        source_code: str,
        state: dict[str, Any],
        updated_at_utc: datetime,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO checkpoints(country_code, source_code, state_json, updated_at_utc)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(country_code, source_code)
                DO UPDATE SET state_json=excluded.state_json, updated_at_utc=excluded.updated_at_utc
                """,
                (country_code, source_code, json.dumps(state, ensure_ascii=False), updated_at_utc.isoformat()),
            )

    def load_checkpoint(self, *, country_code: str, source_code: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            cur = conn.execute(
                "SELECT state_json FROM checkpoints WHERE country_code = ? AND source_code = ?",
                (country_code, source_code),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return json.loads(row["state_json"])
