from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup


# Regex fallback por si AFM cambia el formato (mantener resiliencia)
EXPORT_HEADER_RE = re.compile(
    r"\b(?P<id>(?:A\d{4}-\d{5})|\d+)\s+"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<time>\d{1,2}:\d{2}:\d{2})\s+"
    r"(?P<ampm>AM|PM)\b",
    re.IGNORECASE,
)

LEI_RE = re.compile(r"\b[0-9A-Z]{20}\b", re.IGNORECASE)
ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", re.IGNORECASE)
PERIOD_END_ISO_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
PERIOD_END_COMPACT_RE = re.compile(r"\b(20\d{2})(\d{2})(\d{2})\b")


@dataclass(frozen=True)
class ExportHit:
    record_id: str
    published_at_raw: str
    published_at_utc: datetime
    issuer_name_raw: Optional[str]
    reporting_year_raw: Optional[str]
    document_filename_raw: Optional[str]
    filing_type_raw: Optional[str]
    filing_type_eng_raw: Optional[str]
    raw_chunk: str  # XML fragment or text chunk for evidence


def _parse_afm_datum_to_utc(datum: str, *, source_tz: ZoneInfo) -> datetime:
    # Example: "3/2/2026 11:10:28 AM"
    dt_local = datetime.strptime(datum.strip(), "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=source_tz)
    return dt_local.astimezone(UTC)


def iter_export_hits(export_text: str, *, source_tz: ZoneInfo) -> list[ExportHit]:
    """
    Preferred: parse AFM export XML:
      <register name="...">
        <vermelding>
          <id>...</id>
          <datum>...</datum>
          <uitgevende-instelling>...</uitgevende-instelling>
          <boekjaar>...</boekjaar>
          <filename>...</filename>
          <objecttype>...</objecttype>
          <objecttype_eng>...</objecttype_eng>
        </vermelding>
        ...
      </register>

    Fallback: regex split if XML parsing fails.
    """
    # 1) Try XML parsing
    try:
        root = ET.fromstring(export_text)
        vermeldingen = root.findall("./vermelding")
        if not vermeldingen:
            vermeldingen = root.findall(".//vermelding")

        hits: list[ExportHit] = []
        for v in vermeldingen:
            rid = (v.findtext("id") or "").strip()
            datum = (v.findtext("datum") or "").strip()
            if not rid or not datum:
                continue

            issuer = (v.findtext("uitgevende-instelling") or "").strip() or None
            boekjaar = (v.findtext("boekjaar") or "").strip() or None
            filename = (v.findtext("filename") or "").strip() or None
            objtype = (v.findtext("objecttype") or "").strip() or None
            objtype_eng = (v.findtext("objecttype_eng") or "").strip() or None

            published_at_utc = _parse_afm_datum_to_utc(datum, source_tz=source_tz)

            raw_chunk = ET.tostring(v, encoding="unicode")

            hits.append(
                ExportHit(
                    record_id=rid,
                    published_at_raw=datum,
                    published_at_utc=published_at_utc,
                    issuer_name_raw=issuer,
                    reporting_year_raw=boekjaar,
                    document_filename_raw=filename,
                    filing_type_raw=objtype,
                    filing_type_eng_raw=objtype_eng,
                    raw_chunk=raw_chunk,
                )
            )

        if hits:
            return hits
    except Exception:
        # fall through to regex fallback
        pass

    # 2) Fallback: regex split
    hits: list[ExportHit] = []
    matches = list(EXPORT_HEADER_RE.finditer(export_text))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(export_text)
        chunk = export_text[start:end].strip()

        record_id = m.group("id")
        dt_raw = f"{m.group('date')} {m.group('time')} {m.group('ampm').upper()}"
        published_at_utc = _parse_afm_datum_to_utc(dt_raw, source_tz=source_tz)

        hits.append(
            ExportHit(
                record_id=record_id,
                published_at_raw=dt_raw,
                published_at_utc=published_at_utc,
                issuer_name_raw=None,
                reporting_year_raw=None,
                document_filename_raw=None,
                filing_type_raw=None,
                filing_type_eng_raw=None,
                raw_chunk=chunk,
            )
        )
    return hits


def parse_detail_html(detail_html: str, *, base_url: str = "https://www.afm.nl") -> dict:
    """
    Download stage: extract download URL + filename + (optional) issuer/type from detail page.
    """
    soup = BeautifulSoup(detail_html, "lxml")
    text = soup.get_text("\n", strip=True)

    def pick_after(label: str) -> Optional[str]:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for idx, ln in enumerate(lines):
            if ln.startswith(label):
                rest = ln[len(label) :].strip()
                if rest:
                    return rest
                if idx + 1 < len(lines):
                    return lines[idx + 1]
        return None

    issuer = pick_after("Issuing institution")
    reporting_year = pick_after("Reporting year")

    a = soup.find("a", href=lambda x: isinstance(x, str) and "downloadregisterfile.aspx" in x)
    download_url = None
    filename = None
    if a is not None:
        href = a.get("href")
        if href:
            download_url = href if href.startswith("http") else base_url.rstrip("/") + href
        filename = a.get_text(strip=True) or None

    filing_type = None
    m = re.search(r"Type of document\s+(.+?)\s+Document", text)
    if m:
        filing_type = m.group(1).strip()

    return {
        "issuer_name_raw": issuer,
        "reporting_year_raw": reporting_year,
        "filing_type_raw": filing_type,
        "document_filename_raw": filename,
        "download_url": download_url,
    }


def extract_lei(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = LEI_RE.search(text.upper())
    return m.group(0).upper() if m else None


def extract_isin(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = ISIN_RE.search(text.upper())
    return m.group(0).upper() if m else None


def extract_language(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    low = filename.lower()
    if "-en-" in low or "_en_" in low:
        return "en"
    if "-nl-" in low or "_nl_" in low:
        return "nl"
    return None


def extract_period_end(filename: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not filename:
        return None, None
    m = PERIOD_END_ISO_RE.search(filename)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return m.group(0), f"{y}-{mo}-{d}"
    m = PERIOD_END_COMPACT_RE.search(filename)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        return m.group(0), f"{y}-{mo}-{d}"
    return None, None