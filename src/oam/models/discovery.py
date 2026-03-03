from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class DiscoveryRecord(BaseModel):
    discovery_id: str
    crawl_run_id: str
    country_code: str
    source_code: str
    source_name: str

    source_record_id_raw: Optional[str] = None
    issuer_name_raw: Optional[str] = None
    issuer_id_raw: Optional[str] = None
    isin: Optional[str] = None
    lei: Optional[str] = None

    filing_type_raw: Optional[str] = None
    title_raw: Optional[str] = None

    published_at_raw: Optional[str] = None
    published_at_utc: Optional[datetime] = None

    period_end_raw: Optional[str] = None
    period_end_date: Optional[str] = None  # keep as ISO date string for bronze simplicity

    language_raw: Optional[str] = None

    detail_url: Optional[str] = None
    download_url: Optional[str] = None

    metadata_payload_raw: Any = Field(default=None, description="Raw payload from discovery (JSON/HTML fragment/etc.)")

    discovered_at_utc: datetime
    discovery_status: str

    @field_validator("published_at_utc", "discovered_at_utc")
    @classmethod
    def _tz_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC)")
        return v
