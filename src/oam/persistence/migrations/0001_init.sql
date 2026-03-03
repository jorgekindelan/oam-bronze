PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS crawl_runs (
  crawl_run_id TEXT PRIMARY KEY,
  started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT,
  git_sha TEXT,
  config_hash TEXT
);

CREATE TABLE IF NOT EXISTS discovery_records (
  discovery_id TEXT PRIMARY KEY,
  crawl_run_id TEXT NOT NULL,
  country_code TEXT NOT NULL,
  source_code TEXT NOT NULL,
  source_name TEXT NOT NULL,

  source_record_id_raw TEXT,
  issuer_name_raw TEXT,
  issuer_id_raw TEXT,
  isin TEXT,
  lei TEXT,

  filing_type_raw TEXT,
  title_raw TEXT,

  published_at_raw TEXT,
  published_at_utc TEXT,

  period_end_raw TEXT,
  period_end_date TEXT,

  language_raw TEXT,
  detail_url TEXT,
  download_url TEXT,

  metadata_payload_raw TEXT,

  discovered_at_utc TEXT NOT NULL,
  discovery_status TEXT NOT NULL,

  FOREIGN KEY(crawl_run_id) REFERENCES crawl_runs(crawl_run_id)
);

CREATE INDEX IF NOT EXISTS idx_discovery_country_source_pub
  ON discovery_records(country_code, source_code, published_at_utc);

CREATE INDEX IF NOT EXISTS idx_discovery_run
  ON discovery_records(crawl_run_id);

CREATE TABLE IF NOT EXISTS document_records (
  document_id TEXT NOT NULL,
  version_no INTEGER NOT NULL,

  discovery_id TEXT NOT NULL,
  crawl_run_id TEXT NOT NULL,
  country_code TEXT NOT NULL,
  source_code TEXT NOT NULL,

  issuer_name_raw TEXT,
  isin TEXT,
  lei TEXT,

  filing_type_raw TEXT,
  title_raw TEXT,
  published_at_utc TEXT,

  download_url TEXT,
  final_url TEXT,

  http_status INTEGER,
  mime_type TEXT,
  file_extension TEXT,

  bytes INTEGER,
  sha256 TEXT,
  storage_key TEXT,

  downloaded_at_utc TEXT NOT NULL,
  download_status TEXT NOT NULL,

  error_code TEXT,
  error_message TEXT,
  version_hint TEXT,

  content_disposition TEXT,
  etag TEXT,
  last_modified TEXT,

  PRIMARY KEY(document_id, version_no),
  FOREIGN KEY(discovery_id) REFERENCES discovery_records(discovery_id),
  FOREIGN KEY(crawl_run_id) REFERENCES crawl_runs(crawl_run_id)
);

CREATE INDEX IF NOT EXISTS idx_document_sha
  ON document_records(sha256);

CREATE INDEX IF NOT EXISTS idx_document_discovery
  ON document_records(discovery_id);

CREATE INDEX IF NOT EXISTS idx_document_country_source_pub
  ON document_records(country_code, source_code, published_at_utc);

CREATE TABLE IF NOT EXISTS download_events (
  event_id TEXT PRIMARY KEY,
  crawl_run_id TEXT NOT NULL,
  discovery_id TEXT,
  document_id TEXT,
  version_no INTEGER,
  event_at_utc TEXT NOT NULL,
  stage TEXT NOT NULL,  -- discover|download|persist
  status TEXT NOT NULL, -- ok|error|skipped|duplicate
  http_status INTEGER,
  bytes INTEGER,
  sha256 TEXT,
  error_code TEXT,
  error_message TEXT,

  FOREIGN KEY(crawl_run_id) REFERENCES crawl_runs(crawl_run_id)
);

CREATE INDEX IF NOT EXISTS idx_events_run
  ON download_events(crawl_run_id, stage, status);

CREATE TABLE IF NOT EXISTS checkpoints (
  country_code TEXT NOT NULL,
  source_code TEXT NOT NULL,
  state_json TEXT NOT NULL,
  updated_at_utc TEXT NOT NULL,
  PRIMARY KEY(country_code, source_code)
);
