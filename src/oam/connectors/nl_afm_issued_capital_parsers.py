from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
import csv
import io

from bs4 import BeautifulSoup


MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "mrt": 3, "apr": 4, "may": 5, "mei": 5,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "oct": 10,
    "nov": 11, "dec": 12,
}


DETAIL_ID_RE = re.compile(r"details\?id=(\d+)")


def parse_ui_date_dd_mmm_yyyy(text: str, *, tz: ZoneInfo) -> datetime:
    # e.g. "27 feb 2026", "31 oct 2025"
    parts = text.strip().split()
    if len(parts) != 3:
        raise ValueError(f"Cannot parse date: {text!r}")
    dd = int(parts[0])
    mmm = parts[1].lower()
    yyyy = int(parts[2])
    mm = MONTH_MAP.get(mmm)
    if not mm:
        raise ValueError(f"Unknown month token: {mmm!r} in {text!r}")
    # store as local midnight for consistency
    return datetime(yyyy, mm, dd, 0, 0, 0, tzinfo=tz).astimezone(ZoneInfo("UTC"))


@dataclass(frozen=True)
class ListRow:
    record_id: str
    date_utc: datetime
    date_raw: str
    issuer_name_raw: str
    place_raw: Optional[str]
    detail_url: str


def parse_list_page(html: str, *, base_url: str, tz: ZoneInfo) -> tuple[list[ListRow], list[str]]:
    """
    Parse the list page:
      - rows with links to details?id=...
      - next page URLs (pagination)
    """
    soup = BeautifulSoup(html, "lxml")

    rows: list[ListRow] = []

    # Heurística: cualquier <a href="...details?id=..."> dentro de la tabla
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = DETAIL_ID_RE.search(href)
        if not m:
            continue
        record_id = m.group(1)
        detail_url = href if href.startswith("http") else base_url.rstrip("/") + href

        # La fecha suele ser el texto del link (ej "27 feb 2026")
        date_raw = a.get_text(strip=True)
        try:
            date_utc = parse_ui_date_dd_mmm_yyyy(date_raw, tz=tz)
        except Exception:
            # si el link no era el de fecha, ignora
            continue

        # En la fila hay columnas: Date / Issuer / Place.
        # Subimos a <tr> para extraer celdas.
        tr = a.find_parent("tr")
        if not tr:
            continue
        tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        # Esperado: [date, issuer, place]
        issuer = tds[1] if len(tds) >= 2 else None
        place = tds[2] if len(tds) >= 3 else None
        if not issuer:
            continue

        rows.append(
            ListRow(
                record_id=record_id,
                date_utc=date_utc,
                date_raw=date_raw,
                issuer_name_raw=issuer,
                place_raw=place,
                detail_url=detail_url,
            )
        )

    # Pagination: recoger hrefs que apunten a la misma página con algún parámetro (no adivinamos)
    page_urls: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "geplaatst-kapitaal" in href and "details?id=" not in href:
            u = href if href.startswith("http") else base_url.rstrip("/") + href
            page_urls.add(u)

    return rows, sorted(page_urls)


def _reconstruct_rows_if_needed(text: str) -> str:
    """
    AFM CSV a veces viene con múltiples registros en una misma línea separados por:
      ..."0.00000" "2019-02-21 00:00:00";...
    Insertamos saltos de línea antes de cada nuevo YYYY-MM-DD si está pegado.
    """
    return re.sub(r'"\s+"(?=\d{4}-\d{2}-\d{2}\s)', '"\n"', text)


@dataclass(frozen=True)
class ExportRow:
    date_utc: datetime
    date_raw: str
    issuer_name_raw: str
    kvk_raw: Optional[str]
    place_raw: Optional[str]
    total_placed_capital_raw: Optional[str]
    total_votes_raw: Optional[str]
    number_certified_raw: Optional[str]


def parse_export_csv(text: str, *, tz: ZoneInfo) -> list[ExportRow]:
    fixed = _reconstruct_rows_if_needed(text)
    f = io.StringIO(fixed)
    reader = csv.reader(f, delimiter=";", quotechar='"')
    rows: list[ExportRow] = []

    for r in reader:
        if not r:
            continue
        # Expected 7 columns (from observed data fragments):
        # date; issuer; kvk; place; total_placed_capital; total_votes; number_certified
        if len(r) < 6:
            continue
        date_raw = r[0].strip()
        try:
            dt_local = datetime.strptime(date_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
            date_utc = dt_local.astimezone(ZoneInfo("UTC"))
        except Exception:
            continue

        issuer = r[1].strip() if len(r) > 1 else ""
        kvk = (r[2].strip() if len(r) > 2 else "") or None
        place = (r[3].strip() if len(r) > 3 else "") or None
        total_cap = (r[4].strip() if len(r) > 4 else "") or None
        total_votes = (r[5].strip() if len(r) > 5 else "") or None
        num_cert = (r[6].strip() if len(r) > 6 else "") or None

        rows.append(
            ExportRow(
                date_utc=date_utc,
                date_raw=date_raw,
                issuer_name_raw=issuer,
                kvk_raw=kvk,
                place_raw=place,
                total_placed_capital_raw=total_cap,
                total_votes_raw=total_votes,
                number_certified_raw=num_cert,
            )
        )

    return rows


def parse_detail_page(html: str) -> dict:
    """
    Extract key fields from detail page: issuer, kvk, place, and ISINs from tables.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)

    def pick_after(label: str) -> Optional[str]:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            if ln.startswith(label):
                rest = ln[len(label):].strip()
                if rest:
                    return rest
                if i + 1 < len(lines):
                    return lines[i + 1].strip()
        return None

    issuer = pick_after("Issuing institution")
    kvk = pick_after("Chamber of Commerce")
    place = pick_after("Place of residence")
    date_tx = pick_after("Date of transaction")

    # Parse ISINs appearing in tables
    isins: set[str] = set()
    for td in soup.find_all(["td", "th"]):
        s = td.get_text(" ", strip=True)
        if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", s):
            isins.add(s)

    return {
        "issuer_name_raw": issuer,
        "kvk_raw": kvk,
        "place_raw": place,
        "date_of_transaction_raw": date_tx,
        "isins": sorted(isins),
    }