from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RawPayloadStore:
    root: Path

    def write_json(self, storage_key: str, payload: Any) -> None:
        path = self.root / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def write_text(self, storage_key: str, text: str) -> None:
        path = self.root / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
