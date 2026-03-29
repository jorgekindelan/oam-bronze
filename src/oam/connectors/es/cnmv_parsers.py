from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Shared parsing utilities for all ES / CNMV connectors.
#
# CNMV date format is DD/MM/YYYY throughout the portal.
# verdocumento endpoints return PDFs via GET; HEAD returns 405 — do not HEAD.
# ─────────────────────────────────────────────────────────────────────────────

import re
from datetime import UTC, datetime
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")
NREG_RE = re.compile(r"[?&]nreg=(\d+)", re.IGNORECASE)
VERDOC_E_RE = re.compile(r"verdocumento/ver\?e=[^\"'\s&]+", re.IGNORECASE)
VERDOC_T_RE = re.compile(r"verdocumento/ver\?t=[^\"'\s]+", re.IGNORECASE)

CNMV_BASE = "https://www.cnmv.es"

# NIF/CIF: 8 digits + letter (natural person) or letter + 7 digits + letter/digit (legal entity)
_NIF_NATURAL_RE = re.compile(r"^\d{8}[A-HJ-NP-TV-Z]$", re.IGNORECASE)
_NIF_LEGAL_RE = re.compile(r"^[A-HJ-NP-SU-W]\d{7}[0-9A-J]$", re.IGNORECASE)


# ── Date / time helpers ───────────────────────────────────────────────────────


def parse_cnmv_date(s: str) -> datetime:
    """Parse DD/MM/YYYY (or DD/MM/YYYY HH:MM) to UTC-aware datetime.

    CNMV times are in Europe/Madrid but for bronze purposes we store the date
    at midnight UTC — the time of day is not material for these filings.
    Raises ValueError if the string cannot be parsed.
    """
    s = s.strip()
    # Accept "DD/MM/YYYY HH:MM" or "DD/MM/YYYY HH:MM:SS" variants
    if " " in s:
        date_part, time_part = s.split(" ", 1)
    else:
        date_part = s
        time_part = None

    parts = date_part.split("/")
    if len(parts) != 3:
        raise ValueError(f"Cannot parse CNMV date: {s!r}")
    day, month, year = int(parts[0]), int(parts[1]), int(parts[2])

    if time_part:
        tp = time_part.strip().split(":")
        hour = int(tp[0]) if len(tp) >= 1 else 0
        minute = int(tp[1]) if len(tp) >= 2 else 0
        second = int(tp[2]) if len(tp) >= 3 else 0
    else:
        hour, minute, second = 0, 0, 0

    # Store as UTC; CNMV portal publishes in Europe/Madrid time but we keep
    # it simple at the bronze layer (raw string preserved alongside).
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def parse_cnmv_datetime(date_str: str, time_str: str) -> datetime:
    """Parse separate DD/MM/YYYY date + HH:MM time fields from CNMV HTML tables."""
    combined = f"{date_str.strip()} {time_str.strip()}"
    return parse_cnmv_date(combined)


# ── Identifier helpers ────────────────────────────────────────────────────────


def extract_isin(raw: Optional[str]) -> Optional[str]:
    """Return first valid ISIN found in raw, or None."""
    if not raw:
        return None
    m = ISIN_RE.search(raw.upper())
    return m.group(0).upper() if m else None


def extract_nreg(url: str) -> Optional[str]:
    """Extract nreg=XXXXXXXX from a CNMV relative or absolute URL."""
    m = NREG_RE.search(url)
    return m.group(1) if m else None


def extract_verdocumento_url(href: str) -> Optional[str]:
    """
    Normalise a verdocumento href to an absolute URL.

    Accepts:
      - Relative paths starting with 'verdocumento/...' or '../../verdocumento/...'
      - Absolute https://www.cnmv.es/webservices/verdocumento/...
      - ver?e=ENCODED and ver?t={GUID} forms
    Returns None if no verdocumento pattern is found.
    """
    if not href:
        return None
    href = href.strip()
    # Already absolute
    if href.lower().startswith("http"):
        if "verdocumento/ver" in href.lower():
            return href
        return None
    # Relative — normalise against base; must be the download endpoint, not verDocumento.aspx
    if "verdocumento/ver" not in href.lower():
        return None
    # Strip any leading ../../ traversals
    clean = re.sub(r"^(?:\.\./)+", "", href)
    # Ensure webservices prefix
    if not clean.startswith("webservices/"):
        clean = "webservices/" + clean
    return CNMV_BASE + "/" + clean


def is_valid_nif(nif: str) -> bool:
    """Return True if nif matches Spanish NIF (natural person) or CIF (legal entity)."""
    nif = nif.strip().upper()
    return bool(_NIF_NATURAL_RE.match(nif) or _NIF_LEGAL_RE.match(nif))


# ── Period helpers ────────────────────────────────────────────────────────────

# Matches: "II semestre de 2022-2023 individual" / "I semestre 2023" /
#          "Informe trimestral 2024" / "Informe anual 2023"
_SEMESTER_RE = re.compile(
    r"\b(?P<ord>[IVX]{1,4})\s+semestre(?:\s+de)?\s+(?P<period>\d{4}(?:-\d{4})?)",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(?:ejercicio|año|year)?\s*(?P<year>20\d{2})\b", re.IGNORECASE)
_TRIMESTRAL_RE = re.compile(
    r"\b(?:informe\s+trimestral|trimestre[^\d]*(?P<q>\d))[^\d]*(?P<year>20\d{2})\b",
    re.IGNORECASE,
)

# Map semester roman numeral → last month of that semester half-year
_SEMESTER_LAST_MONTH = {
    "i": "06-30",
    "ii": "12-31",
}
_QUARTER_LAST_MONTH = {
    "1": "03-31",
    "2": "06-30",
    "3": "09-30",
    "4": "12-31",
}


def extract_period_end_from_tipo(tipo: str) -> tuple[Optional[str], Optional[str]]:
    """
    Extract (period_end_raw, period_end_date_iso) from a CNMV IFI tipo string.

    Examples:
      "II semestre de 2022-2023 individual" -> ("2022-2023", "2023-12-31")
      "I semestre 2024"                     -> ("2024", "2024-06-30")
      "Informe trimestral 2024"             -> ("2024", None)  # quarter not identified
      "3T 2024"                             -> ("2024", "2024-09-30")

    The raw period string is always preserved.
    Following FR parser convention: period_end_date is ISO date string or None.
    """
    if not tipo:
        return None, None

    # Semester match
    m = _SEMESTER_RE.search(tipo)
    if m:
        ord_str = m.group("ord").lower()
        period = m.group("period")
        # If fiscal year spans two calendar years ("2022-2023"), use the second year
        if "-" in period:
            end_year = period.split("-")[-1]
        else:
            end_year = period
        last_month = _SEMESTER_LAST_MONTH.get(ord_str)
        if last_month:
            return period, f"{end_year}-{last_month}"
        return period, None

    # Quarter with explicit number e.g. "3T 2024" or "Informe trimestral 3 2024"
    qt = re.search(r"\b(?P<q>[1-4])[TtQq°]\s*(?P<year>20\d{2})\b", tipo)
    if qt:
        q, year = qt.group("q"), qt.group("year")
        last_month = _QUARTER_LAST_MONTH.get(q)
        if last_month:
            return year, f"{year}-{last_month}"
        return year, None

    # Trimestral with year only
    m3 = _TRIMESTRAL_RE.search(tipo)
    if m3:
        year = m3.group("year")
        q = m3.group("q") if m3.lastgroup and m3.group("q") else None
        if q:
            last_month = _QUARTER_LAST_MONTH.get(q)
            if last_month:
                return year, f"{year}-{last_month}"
        return year, None

    # Annual / year only
    m2 = _YEAR_RE.search(tipo)
    if m2:
        year = m2.group("year")
        return year, f"{year}-12-31"

    return None, None
