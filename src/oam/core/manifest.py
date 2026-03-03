from __future__ import annotations

from pathlib import Path

import yaml

from oam.models.manifest import CountryManifest


def load_manifest(path: str | Path) -> CountryManifest:
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return CountryManifest.model_validate(data)
