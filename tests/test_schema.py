from __future__ import annotations

import sqlite3
from pathlib import Path

from oam.persistence.db import SQLiteMetadataStore


def test_init_schema(tmp_path: Path):
    db_path = tmp_path / "oam.sqlite"
    store = SQLiteMetadataStore(db_path)
    store.init_schema()

    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r[0] for r in cur.fetchall()}
    for required in {
        "crawl_runs",
        "discovery_records",
        "document_records",
        "download_events",
        "checkpoints",
    }:
        assert required in names
