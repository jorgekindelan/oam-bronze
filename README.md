# OAM Bronze

Capa bronce (append-only, auditable, replayable) para adquisición de **filings regulatorios oficiales europeos**.

## Quickstart (local)

> Recomendado: `uv` (rápido) o `poetry`. Si usas pip, adapta.

### Con uv

```bash
uv sync
# inicializa DB
uv run oam init-db --db ./data/oam.sqlite
# ejecuta un run de ejemplo (DummyConnector)
uv run oam run --country ZZ --source DUMMY --from 2015-01-01 --to 2015-01-02 --db ./data/oam.sqlite --root ./data
```

## Layout de almacenamiento (local filesystem)

- Binarios: `binaries/{country}/{source}/{year}/{month}/{issuer_slug}/{document_id}.{ext}`
- Payloads crudos: `raw_discovery/{country}/{source}/{crawl_run_id}/{discovery_id}.json`
- Snapshots: `snapshots/{country}/{source}/{YYYY-MM-DD}/{crawl_run_id}.jsonl`
- Reports: `run_reports/{country}/{source}/{crawl_run_id}/report.json`

