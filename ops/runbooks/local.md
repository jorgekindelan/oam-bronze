# Runbook local

## Inicializar

```bash
uv sync
uv run oam init-db --db ./data/oam.sqlite
```

## Ejecutar (Dummy)

```bash
uv run oam run --country ZZ --source DUMMY --from 2015-01-01 --to 2015-01-02 --db ./data/oam.sqlite --root ./data
```

Salidas:
- `./data/binaries/...` binarios
- `./data/raw_discovery/...` payloads probatorios
- `./data/snapshots/...` snapshot JSONL por run
- `./data/run_reports/.../report.json` métricas del run
