# oam-bronze

**Bronze layer** for European regulatory filings. Discovers, downloads, deduplicates, and stores documents from public regulatory registers — immutably, with full provenance.

---

## What it does

`oam-bronze` implements a two-stage pipeline per data source:

```
Discover ──► Download ──► Store
   │               │          │
  DB (SQLite)    Binaries   Metadata
  discovery      (files)    (SQLite)
  records
```

1. **Discover** — crawls a regulatory register, emits `DiscoveryRecord`s with metadata (issuer, ISIN, LEI, filing type, dates). Records are deduplicated by `discovery_id`.
2. **Download** — fetches the actual document for each pending discovery. Computes SHA-256, handles versioning, and deduplicates identical content across sources.
3. **Store** — immutable filesystem storage partitioned by `country/source/year/month/issuer/`. SQLite tracks all metadata, checksums, and run history.

---

## Data sources

| Country | Source code | Register | Document type |
|---------|-------------|----------|---------------|
| NL | `AFM_FINREP` | AFM financial reporting | Annual/semi-annual reports (PDF, XBRL) |
| NL | `AFM_ISSUED_CAPITAL` | AFM geplaatst kapitaal | Issued capital notifications (HTML) |
| NL | `AFM_INSIDE_INFO` | AFM openbaarmaking voorwetenschap | Inside information disclosures (PDF, DOCX) |
| NL | `AFM_SUBSTANTIAL_HOLDINGS` | AFM substantiële deelnemingen | Substantial holdings & short positions (HTML) |

---

## Project structure

```
oam-bronze/
├── src/oam/
│   ├── cli.py                        # Typer CLI entrypoint
│   ├── connectors/
│   │   ├── base.py                   # BaseConnector interface
│   │   ├── registry.py               # @register decorator + get_connector()
│   │   └── nl/
│   │       ├── __init__.py
│   │       ├── afm_finrep.py         # NL financial reports connector
│   │       ├── afm_finrep_parsers.py
│   │       ├── afm_inside_info.py    # NL inside information connector
│   │       ├── afm_inside_info_parsers.py
│   │       ├── afm_issued_capital.py # NL issued capital connector
│   │       ├── afm_issued_capital_parsers.py
│   │       ├── afm_substantial_holdings.py
│   │       └── afm_substantial_holdings_parsers.py
│   ├── core/
│   │   ├── http.py                   # httpx client + retry helpers
│   │   ├── ids.py                    # Deterministic ID generation (UUID7)
│   │   ├── hashing.py                # SHA-256 helpers
│   │   ├── manifest.py               # YAML manifest loader
│   │   └── time.py                   # UTC helpers
│   ├── models/
│   │   ├── discovery.py              # DiscoveryRecord (Pydantic)
│   │   ├── document.py               # DocumentRecord (Pydantic)
│   │   ├── manifest.py               # SourceConfig (Pydantic)
│   │   └── run.py                    # RunReport
│   ├── persistence/
│   │   ├── db.py                     # SQLiteMetadataStore
│   │   ├── filesystem_store.py       # Binary object store (filesystem)
│   │   └── raw_payload_store.py      # Raw JSON payload store
│   └── pipelines/
│       └── orchestrator.py           # run_discovery() / run_download()
├── country_manifests/
│   ├── sources.nl.yml                # NL source configuration
│   └── sources.fr.yml                # FR source configuration (placeholder)
├── scripts/                          # Ad-hoc probe/debug scripts
├── tests/
└── pyproject.toml
```

---

## Setup

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies
uv sync

# Install in editable mode (includes dev tools)
uv sync --group dev
```

---

## Usage

### List available connectors

```bash
uv run oam list-connectors
```

```
NL/AFM_FINREP
NL/AFM_INSIDE_INFO
NL/AFM_ISSUED_CAPITAL
NL/AFM_SUBSTANTIAL_HOLDINGS
```

### Initialise the metadata DB

```bash
uv run oam init-db --db data/oam.sqlite
```

### Run discovery + download

```bash
uv run oam run \
  --country NL \
  --source AFM_FINREP \
  --from 2026-01-01 \
  --to 2026-03-31 \
  --db data/oam.sqlite \
  --root data/ \
  --manifest country_manifests/sources.nl.yml
```

| Flag | Description |
|------|-------------|
| `--country` | ISO country code (`NL`, `FR`, …) |
| `--source` | Source code from the manifest |
| `--from` / `--to` | Date window for discovery (ISO 8601) |
| `--db` | Path to SQLite metadata file |
| `--root` | Root directory for binary and raw storage |
| `--manifest` | Path to country YAML manifest |
| `--mode` | `discover`, `download`, or `full` (default) |
| `--limit` | Max downloads per run (default: 1000) |
| `--download-ignore-window` | Drain the full backlog regardless of date window |

### Discovery only

```bash
uv run oam run \
  --country NL --source AFM_INSIDE_INFO \
  --from 2026-03-01 --to 2026-03-28 \
  --db data/oam.sqlite --root data/ \
  --manifest country_manifests/sources.nl.yml \
  --mode discover
```

---

## Storage layout

```
data/
├── binaries/
│   └── NL/AFM_FINREP/2026/03/<issuer-slug>/<document_id>.pdf
├── raw_discovery/
│   └── NL/AFM_FINREP/<crawl_run_id>/<discovery_id>.json
├── snapshots/
│   └── NL/AFM_FINREP/2026-03-28/<crawl_run_id>.jsonl
└── run_reports/
    └── NL/AFM_FINREP/<crawl_run_id>/report.json
```

---

## Data model

### DiscoveryRecord

| Field | Description |
|-------|-------------|
| `discovery_id` | Deterministic hash of `(country, source, record_id, detail_url)` |
| `source_record_id_raw` | Raw ID from the register |
| `issuer_name_raw` | Issuer name as returned by the source |
| `isin` / `lei` | Extracted identifiers (best-effort) |
| `filing_type_raw` | Filing type as returned by the source |
| `published_at_utc` | Publication timestamp (UTC, timezone-aware) |
| `detail_url` | URL of the register detail page |
| `download_url` | URL of the actual document (if known at discovery) |
| `metadata_payload_raw` | Raw source-specific metadata (stored as JSON) |

### DocumentRecord

| Field | Description |
|-------|-------------|
| `document_id` | Deterministic hash of `(discovery_id, download_url)` |
| `version_no` | Increments when the same logical document changes content |
| `sha256` | SHA-256 of the stored bytes |
| `storage_key` | Relative path within `data/binaries/` |
| `download_status` | `DOWNLOADED`, `DUPLICATE_SHA`, or error status |

---

## Adding a new connector

1. Create `src/oam/connectors/<country_code>/` with an `__init__.py` that imports the connector module.
2. Implement a class inheriting `BaseConnector` with `@register`:

```python
from oam.connectors.base import BaseConnector
from oam.connectors.registry import register

@register
class MyConnector(BaseConnector):
    country_code = "XX"
    source_code = "MY_SOURCE"
    source_name = "My Register"

    async def discover(self, *, crawl_run_id, date_from, date_to, checkpoint):
        # yield DiscoveryRecord(...)
        ...

    async def download_document(self, *, crawl_run_id, discovery):
        # return (DocumentRecord(...), bytes_content)
        ...
```

3. Import the new module in `src/oam/connectors/<country_code>/__init__.py`.
4. Import the country package in `src/oam/cli.py`.
5. Add the source configuration to `country_manifests/sources.<cc>.yml`.

---

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check src/

# Type check
uv run mypy src/
```

---

## License

Proprietary — all rights reserved.
