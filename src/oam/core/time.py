from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional

from dateutil import parser


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def parse_datetime_to_utc(raw: Optional[str]) -> Optional[datetime]:
    """Best-effort parse to timezone-aware UTC datetime.

    - Keeps behaviour conservative: if parsing fails, returns None.
    - If the parsed datetime is naive, assumes UTC (we keep raw separately in bronze anyway).
    """
    if raw is None:
        return None
    raw_str = raw.strip()
    if not raw_str:
        return None
    try:
        dt = parser.parse(raw_str)
    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class DateWindow:
    date_from: datetime
    date_to: datetime

    def __post_init__(self) -> None:
        if self.date_from.tzinfo is None or self.date_to.tzinfo is None:
            raise ValueError("DateWindow datetimes must be timezone-aware")
        if self.date_from > self.date_to:
            raise ValueError("date_from must be <= date_to")
