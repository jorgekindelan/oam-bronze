from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import typer

from oam.connectors import dummy as _dummy 
from oam.connectors import nl_afm_finrep as _nl_afm_finrep  # noqa: F401 (registers connector)# noqa: F401 (registers connector)
from oam.connectors.registry import get_connector, list_connectors
from oam.core.ids import new_crawl_run_id
from oam.core.manifest import load_manifest
from oam.core.time import now_utc
from oam.models.manifest import SourceConfig
from oam.models.run import RunReport
from oam.persistence.db import SQLiteMetadataStore
from oam.persistence.filesystem_store import FilesystemStore
from oam.persistence.raw_payload_store import RawPayloadStore
from oam.pipelines.orchestrator import RuntimePaths, run_discovery, run_download, write_report


app = typer.Typer(add_completion=False)


def _parse_date(date_str: str, *, end_of_day: bool) -> datetime:
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59, tzinfo=UTC)
        else:
            dt = dt.replace(hour=0, minute=0, second=0, tzinfo=UTC)
    return dt.astimezone(UTC)


@app.command("list-connectors")
def cmd_list_connectors() -> None:
    for cc, sc in list_connectors():
        typer.echo(f"{cc}/{sc}")


@app.command("init-db")
def cmd_init_db(db: Path = typer.Option(..., help="SQLite DB path")) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteMetadataStore(db)
    store.init_schema()
    typer.echo(f"OK: initialized {db}")


@app.command("run")
def cmd_run(
    country: str = typer.Option(..., "--country"),
    source: str = typer.Option(..., "--source"),
    date_from: str = typer.Option(..., "--from"),
    date_to: str = typer.Option(..., "--to"),
    mode: str = typer.Option("full", help="discover|download|full"),
    db: Path = typer.Option(..., help="SQLite DB path"),
    root: Path = typer.Option(..., help="Root storage path"),
    limit: int = typer.Option(1000, help="Max downloads per run"),
    manifest: Optional[Path] = typer.Option(None, help="Path to country manifest YAML"),
    download_ignore_window: bool = typer.Option(
        False,
        "--download-ignore-window",
        help="If set, download will ignore --from/--to and drain the oldest pending backlog for this source.",
    ),
) -> None:
    """Run discovery and/or download for a given country/source."""

    async def _run() -> None:
        crawl_run_id = new_crawl_run_id()
        started = now_utc()

        store = SQLiteMetadataStore(db)
        store.init_schema()
        store.record_crawl_run_start(crawl_run_id=crawl_run_id, started_at_utc=started)

        obj_store = FilesystemStore(root)
        raw_store = RawPayloadStore(root)
        paths = RuntimePaths(root=root)

        config: dict[str, Any] = {}

        if manifest:
            mf = load_manifest(manifest)
            if mf.country_code != country:
                raise typer.BadParameter(
                    f"Manifest country_code={mf.country_code} does not match --country={country}"
                )
            sc: Optional[SourceConfig] = None
            for s in mf.sources:
                if s.source_code == source and s.enabled:
                    sc = s
                    break
            if sc is None:
                raise typer.BadParameter(f"Source {source} not found/enabled in manifest")
            config = sc.model_dump()

        connector = get_connector(country_code=country, source_code=source, config=config)

        report = RunReport(
            crawl_run_id=crawl_run_id,
            country_code=country,
            source_code=source,
            started_at_utc=started,
        )

        df = _parse_date(date_from, end_of_day=False)
        dt = _parse_date(date_to, end_of_day=True)

        if mode in ("discover", "full"):
            await run_discovery(
                db=store,
                raw_store=raw_store,
                connector=connector,
                crawl_run_id=crawl_run_id,
                date_from=df,
                date_to=dt,
                paths=paths,
                report=report,
            )

        if mode in ("download", "full"):
            await run_download(
                db=store,
                obj_store=obj_store,
                connector=connector,
                crawl_run_id=crawl_run_id,
                paths=paths,
                report=report,
                limit=limit,
                date_from=df,
                date_to=dt,
                ignore_date_window=download_ignore_window,
            )

        store.record_crawl_run_finish(crawl_run_id=crawl_run_id, finished_at_utc=now_utc())
        write_report(paths=paths, report=report)

        typer.echo(f"OK: crawl_run_id={crawl_run_id}")

    asyncio.run(_run())
