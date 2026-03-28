from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class DocumentRecord(BaseModel):
    # Deterministic id for a logical document (discovery_id + download_url)
    document_id: str
    version_no: int = 1

    discovery_id: str
    crawl_run_id: str
    country_code: str
    source_code: str

    issuer_name_raw: Optional[str] = None
    isin: Optional[str] = None
    lei: Optional[str] = None

    filing_type_raw: Optional[str] = None
    title_raw: Optional[str] = None
    published_at_utc: Optional[datetime] = None

    download_url: Optional[str] = None
    final_url: Optional[str] = None

    http_status: Optional[int] = None
    mime_type: Optional[str] = None
    file_extension: Optional[str] = None

    bytes: Optional[int] = None
    sha256: Optional[str] = None

    storage_key: Optional[str] = None

    downloaded_at_utc: datetime
    download_status: str

    error_code: Optional[str] = None
    error_message: Optional[str] = None

    version_hint: Optional[str] = None

    content_disposition: Optional[str] = None
    etag: Optional[str] = None
    last_modified: Optional[str] = None

    @field_validator("published_at_utc", "downloaded_at_utc")
    @classmethod
    def _tz_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC)")
        return v

    @field_validator("version_no")
    @classmethod
    def _version_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("version_no must be >= 1")
        return v
