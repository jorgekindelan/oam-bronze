from __future__ import annotations

import re


_slug_re = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_len: int = 80) -> str:
    """Deterministic, filesystem-safe slug.

    Conservative: ASCII lowercase, digits, dash, trimmed length.
    """
    v = (value or "").strip().lower()
    v = v.encode("utf-8", errors="ignore").decode("utf-8")
    v = _slug_re.sub("-", v).strip("-")
    if not v:
        v = "unknown"
    return v[:max_len]
