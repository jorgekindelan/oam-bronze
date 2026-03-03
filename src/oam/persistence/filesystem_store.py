from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional

from oam.core.text import slugify
from oam.persistence.object_store import ObjectStore


@dataclass(frozen=True)
class FilesystemStore(ObjectStore):
    root: Path

    def exists(self, storage_key: str) -> bool:
        return (self.root / storage_key).exists()

    def put_bytes(
        self,
        storage_key: str,
        data: bytes,
        *,
        metadata: Optional[Mapping[str, str]] = None,
        overwrite: bool = False,
    ) -> None:
        target = self.root / storage_key
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists() and not overwrite:
            return

        tmp = target.with_suffix(target.suffix + ".tmp")
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)

        # Optional sidecar metadata (best-effort)
        if metadata:
            meta_path = target.with_suffix(target.suffix + ".meta.json")
            try:
                import json

                meta_path.write_text(json.dumps(dict(metadata), ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass


def build_binary_storage_key(
    *,
    country_code: str,
    source_code: str,
    published_at_utc: Optional[datetime],
    issuer_name_raw: Optional[str],
    document_id: str,
    file_extension: Optional[str],
) -> str:
    # Partitioning chooses published_at when available; else falls back to current year/month bucket.
    dt = published_at_utc
    if dt is None:
        dt = datetime.utcnow()
    year = f"{dt.year:04d}"
    month = f"{dt.month:02d}"
    issuer_slug = slugify(issuer_name_raw or "unknown")

    ext = (file_extension or "bin").lstrip(".").lower()
    return f"binaries/{country_code}/{source_code}/{year}/{month}/{issuer_slug}/{document_id}.{ext}"
