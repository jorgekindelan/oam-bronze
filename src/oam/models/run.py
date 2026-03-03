from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, field_validator


class CheckpointState(BaseModel):
    country_code: str
    source_code: str
    state: Dict[str, Any]
    updated_at_utc: datetime

    @field_validator("updated_at_utc")
    @classmethod
    def _tz_aware(cls, v: datetime):
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC)")
        return v


class RunReport(BaseModel):
    crawl_run_id: str
    country_code: str
    source_code: str
    started_at_utc: datetime
    finished_at_utc: Optional[datetime] = None

    discovered_count: int = 0
    discovery_error_count: int = 0

    download_candidate_count: int = 0
    downloaded_count: int = 0
    download_error_count: int = 0
    duplicate_sha_count: int = 0

    bytes_downloaded: int = 0

    @field_validator("started_at_utc", "finished_at_utc")
    @classmethod
    def _tz_aware(cls, v: Optional[datetime]):
        if v is None:
            return v
        if v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware (UTC)")
        return v
