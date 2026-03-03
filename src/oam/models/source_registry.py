from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SourceRegistryEntry(BaseModel):
    country_code: str
    source_code: str
    source_name: str

    operator: Optional[str] = None
    base_url: Optional[str] = None
    access_type: str  # api|html|feed|export|hybrid

    supports_historical_from: Optional[str] = None  # ISO date string
    notes_legal: Optional[str] = None
    notes_technical: Optional[str] = None

    maturity: str = "draft"  # draft|beta|prod
