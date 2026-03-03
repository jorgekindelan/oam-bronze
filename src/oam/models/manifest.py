from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class SourceConfig(BaseModel):
    source_code: str
    source_name: str
    enabled: bool = True

    # runtime / http controls
    throttle_rps: float = Field(default=2.0, ge=0.0)
    timeout_s: int = Field(default=30, ge=1)

    # optional HTTP headers, per source
    headers: Dict[str, str] = Field(default_factory=dict)

    # free-form extras, source-specific knobs without breaking schema
    extra: Dict[str, Any] = Field(default_factory=dict)


class CountryManifest(BaseModel):
    country_code: str
    sources: List[SourceConfig]
