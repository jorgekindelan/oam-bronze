from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from oam.core.logging import get_logger

logger = get_logger("oam.fr.info_financiere_parsers")

BUCKET1_SOUS_TYPE = "Rapports financiers et d'audit annuels"
DUMMY_ISIN = "999999999999"
ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", re.IGNORECASE)
PERIOD_END_ISO_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
PERIOD_END_COMPACT_RE = re.compile(r"\b(20\d{2})(\d{2})(\d{2})\b")
# French date format: DD/MM/YYYY — common in B5 voting-rights titles
# e.g. "Nombre total de droits de vote au 31/01/2024" → 2024-01-31
PERIOD_END_FRENCH_RE = re.compile(r"\b(\d{1,2})/(\d{2})/(20\d{2})\b")
YEAR_ONLY_RE = re.compile(r"\b(20\d{2})\b")

_LANG_MAP = {
    "français": "fr",
    "french": "fr",
    "english": "en",
    "anglais": "en",
    "allemand": "de",
    "german": "de",
    "néerlandais": "nl",
    "dutch": "nl",
    "espagnol": "es",
    "spanish": "es",
    "italien": "it",
    "italian": "it",
}


@dataclass(frozen=True)
class SearchHit:
    uin_idt_uin: str
    published_at_raw: str
    published_at_utc: datetime
    issuer_name_raw: Optional[str]
    isin_raw: Optional[str]
    lei_raw: Optional[str]
    title_raw: Optional[str]
    language_raw_api: Optional[str]
    filing_type_raw: str
    download_url: Optional[str]
    fichier_nom_raw: Optional[str]
    api_record_raw: dict  # full raw record for provenance


def parse_uin_dat_amf(raw: str) -> datetime:
    """Parse ISO 8601 string with timezone offset and convert to UTC."""
    try:
        dt = datetime.fromisoformat(raw)
        return dt.astimezone(UTC)
    except Exception as exc:
        raise ValueError(f"Cannot parse uin_dat_amf: {raw!r}") from exc


def parse_records(response_json: dict) -> list[SearchHit]:
    """Parse OpenDataSoft API response into SearchHit list."""
    results = response_json.get("results", [])
    hits: list[SearchHit] = []
    for r in results:
        uin = (r.get("uin_idt_uin") or "").strip()
        if not uin:
            logger.warning("infofin_missing_uin_idt_uin", extra={"record": r})
            continue

        dat_raw = (r.get("uin_dat_amf") or "").strip()
        if not dat_raw:
            logger.warning("infofin_missing_uin_dat_amf", extra={"uin_idt_uin": uin})
            continue

        published_at_utc = parse_uin_dat_amf(dat_raw)

        issuer = (r.get("identificationsociete_iso_nom_soc") or "").strip() or None
        isin_raw = (r.get("identificationsociete_iso_cd_isi") or "").strip() or None
        lei_raw = (r.get("identificationsociete_iso_cd_lei") or "").strip() or None
        title_raw = (r.get("informationdeposee_inf_tit_inf") or "").strip() or None
        language_raw_api = (r.get("informationdeposee_inf_lng_inf") or "").strip() or None
        filing_type_raw = (r.get("sous_type_d_information") or "").strip()
        download_url = (r.get("url_de_recuperation") or "").strip() or None
        fichier_nom_raw = (r.get("fichierdecontenu_inf_fic_nom") or "").strip() or None

        hits.append(
            SearchHit(
                uin_idt_uin=uin,
                published_at_raw=dat_raw,
                published_at_utc=published_at_utc,
                issuer_name_raw=issuer,
                isin_raw=isin_raw,
                lei_raw=lei_raw,
                title_raw=title_raw,
                language_raw_api=language_raw_api,
                filing_type_raw=filing_type_raw,
                download_url=download_url,
                fichier_nom_raw=fichier_nom_raw,
                api_record_raw=r,
            )
        )

    return hits


def extract_isin(raw: Optional[str]) -> Optional[str]:
    """Extract and validate ISIN from raw string. Returns None for dummy value."""
    if not raw:
        return None
    if raw.strip() == DUMMY_ISIN:
        return None
    m = ISIN_RE.search(raw.upper())
    return m.group(0).upper() if m else None


def extract_issuer_id(isin_raw: Optional[str], lei_raw: Optional[str]) -> Optional[str]:
    """
    Return the best available stable issuer identifier, in priority order:
      1. Real ISIN  → "FR0014000MR3"
      2. LEI        → "LEI:969500NMI4UP00IO8G47"
      3. None       → no standard identifier available (~10% of FR annual reports)

    The LEI: prefix makes the identifier type unambiguous to downstream consumers.
    The dummy ISIN "999999999999" is treated as absent.
    """
    isin = extract_isin(isin_raw)
    if isin:
        return isin
    if lei_raw and lei_raw.strip():
        return f"LEI:{lei_raw.strip()}"
    return None


def extract_language(lang_api: Optional[str]) -> Optional[str]:
    """Map raw API language string to ISO 639-1 code."""
    if not lang_api:
        return None
    return _LANG_MAP.get(lang_api.lower().strip())


def extract_period_end(title_raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """
    Extract period end date from filing title.
    Returns (period_end_raw, period_end_date_iso).
    """
    if not title_raw:
        return None, None

    # Try ISO date pattern first (YYYY-MM-DD)
    m = PERIOD_END_ISO_RE.search(title_raw)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return m.group(0), f"{y}-{mo}-{d}"

    # Try compact date pattern (YYYYMMDD)
    m = PERIOD_END_COMPACT_RE.search(title_raw)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return m.group(0), f"{y}-{mo}-{d}"

    # Try French date pattern (DD/MM/YYYY) — common in B5 voting-rights titles
    # Must precede year-only fallback to avoid extracting wrong year from "31/01/2024"
    m = PERIOD_END_FRENCH_RE.search(title_raw)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return m.group(0), f"{y}-{mo}-{int(d):02d}"

    # Try year-only pattern (YYYY) — infer period end as Dec 31
    m = YEAR_ONLY_RE.search(title_raw)
    if m:
        year = m.group(1)
        return year, f"{year}-12-31"

    return None, None


def is_annual_report(filing_type_raw: Optional[str]) -> bool:
    """Return True if filing_type_raw matches Bucket 1 sous-type."""
    return filing_type_raw is not None and filing_type_raw.strip() == BUCKET1_SOUS_TYPE
