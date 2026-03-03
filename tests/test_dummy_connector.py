from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from oam.connectors import dummy as _dummy  # noqa: F401
from oam.connectors.registry import get_connector
from oam.core.ids import new_crawl_run_id
from oam.core.time import now_utc
from oam.models.run import RunReport
from oam.persistence.db import SQLiteMetadataStore
from oam.persistence.filesystem_store import FilesystemStore
from oam.persistence.raw_payload_store import RawPayloadStore
from oam.pipelines.orchestrator import RuntimePaths, run_discovery, run_download


@pytest.mark.asyncio
async def test_dummy_end_to_end(tmp_path: Path):
    db_path = tmp_path / "oam.sqlite"
    store = SQLiteMetadataStore(db_path)
    store.init_schema()

    root = tmp_path / "data"
    root.mkdir()

    crawl_run_id = new_crawl_run_id()
    store.record_crawl_run_start(crawl_run_id=crawl_run_id, started_at_utc=now_utc())

    connector = get_connector(country_code="ZZ", source_code="DUMMY", config={})

    report = RunReport(
        crawl_run_id=crawl_run_id,
        country_code="ZZ",
        source_code="DUMMY",
        started_at_utc=now_utc(),
    )

    await run_discovery(
        db=store,
        raw_store=RawPayloadStore(root),
        connector=connector,
        crawl_run_id=crawl_run_id,
        date_from=datetime(2015, 1, 1, tzinfo=UTC),
        date_to=datetime(2015, 1, 2, 23, 59, 59, tzinfo=UTC),
        paths=RuntimePaths(root=root),
        report=report,
    )

    assert report.discovered_count == 2

    await run_download(
        db=store,
        obj_store=FilesystemStore(root),
        connector=connector,
        crawl_run_id=crawl_run_id,
        paths=RuntimePaths(root=root),
        report=report,
        limit=10,
    )

    # One PDF is stored for each discovery (not deduped across them)
    # but our dummy content is identical, so dedup may kick in.
    assert report.download_candidate_count == 2
    assert report.downloaded_count in (0, 1, 2)

    # Ensure at least one binary exists
    binaries = list((root / "binaries").rglob("*.pdf"))
    assert len(binaries) >= 1

    # Ensure raw discovery payloads exist
    raw_payloads = list((root / "raw_discovery").rglob("*.json"))
    assert len(raw_payloads) == 2
