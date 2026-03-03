from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Iterable, Optional

from oam.core.hashing import sha256_text


# --- UUIDv7 (RFC 9562) ---
# We generate a UUID that is sortable by time (ms). This is useful as crawl_run_id.

_last_ms: int = -1
_last_rand_a: int = 0


def uuid7() -> uuid.UUID:
    """Generate a UUID version 7 (time-ordered).

    Implementation is self-contained to avoid extra dependencies.
    """
    global _last_ms, _last_rand_a

    ts_ms = int(time.time() * 1000)
    if ts_ms == _last_ms:
        # Monotonic increment inside the same millisecond (12-bit space).
        _last_rand_a = (_last_rand_a + 1) & 0x0FFF
    else:
        _last_ms = ts_ms
        _last_rand_a = secrets.randbits(12)

    rand_b = secrets.randbits(62)

    uuid_int = (
        (ts_ms & ((1 << 48) - 1)) << 80
        | 0x7 << 76
        | (_last_rand_a & 0x0FFF) << 64
        | 0b10 << 62
        | (rand_b & ((1 << 62) - 1))
    )
    return uuid.UUID(int=uuid_int)


def new_crawl_run_id() -> str:
    return str(uuid7())


# --- Deterministic IDs for bronze ---


def stable_id(prefix: str, parts: Iterable[Optional[str]]) -> str:
    """Create a deterministic id as '<prefix>_<sha256>'.

    Notes:
    - This is stable across runs/machines.
    - Uses raw (non-normalized) parts, but trims whitespace.
    - Collisions are practically negligible.
    """
    clean = []
    for p in parts:
        if p is None:
            clean.append("")
        else:
            clean.append(str(p).strip())
    joined = prefix + "|" + "|".join(clean)
    return f"{prefix}_{sha256_text(joined)}"


@dataclass(frozen=True)
class DeterministicIds:
    discovery_id: str
    document_id: str


def make_ids(
    *,
    country_code: str,
    source_code: str,
    source_record_id_raw: Optional[str],
    detail_url: Optional[str],
    download_url: Optional[str],
) -> DeterministicIds:
    discovery_id = stable_id(
        "disc",
        [country_code, source_code, source_record_id_raw, detail_url, download_url],
    )
    document_id = stable_id("doc", [country_code, source_code, discovery_id, download_url])
    return DeterministicIds(discovery_id=discovery_id, document_id=document_id)
