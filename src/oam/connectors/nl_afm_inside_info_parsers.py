from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


DETAIL_ID_RE = re.compile(r"details\?id=([^&]+)")
DT_RE = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3})\s+(?P<year>\d{4})\s*-\s*(?P<h>\d{2}):(?P<m>\d{2})",
    re.IGNORECASE,
)

MONTH_MAP = {
    # EN
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # NL (notably mrt, mei, okt)
    "mrt": 3, "mei": 5, "okt": 10,
}

FILE_EXTS = (".pdf", ".doc", ".docx", ".zip", ".html", ".htm", ".txt", ".xls", ".xlsx", ".ppt", ".pptx")


@dataclass(frozen=True)
class ListHit:
    record_id: str
    published_at_raw: str
    published_at_utc: datetime
    issuer_name_raw: str
    title_raw: str
    detail_url: str


@dataclass(frozen=True)
class RelatedDownload:
    filename: str
    href: str


def parse_publication_dt(text: str, *, source_tz: ZoneInfo) -> datetime:
    """
    Parses strings like:
      '03 mar 2026 - 08:05'
      '03 mrt 2026 - 08:05'
      '5 mei 2011 - 14:04'
    """
    m = DT_RE.search(text.strip())
    if not m:
        raise ValueError(f"Cannot parse publication datetime from: {text!r}")

    day = int(m.group("day"))
    mon_tok = m.group("mon").lower()
    year = int(m.group("year"))
    hh = int(m.group("h"))
    mm = int(m.group("m"))

    mon = MONTH_MAP.get(mon_tok)
    if not mon:
        raise ValueError(f"Unknown month token {mon_tok!r} in {text!r}")

    dt_local = datetime(year, mon, day, hh, mm, 0, tzinfo=source_tz)
    return dt_local.astimezone(UTC)


def parse_list_page(html: str, *, base_url: str, source_tz: ZoneInfo) -> tuple[list[ListHit], Optional[str]]:
    """
    Parse a register list page:
      - rows with date link -> details?id=...
      - find next page URL if possible

    Returns (hits, next_page_url_or_None).
    """
    soup = BeautifulSoup(html, "lxml")
    hits: list[ListHit] = []

    # Each row has a date/time link pointing to details?id=...
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = DETAIL_ID_RE.search(href)
        if not m:
            continue

        date_raw = a.get_text(" ", strip=True)
        try:
            published_at_utc = parse_publication_dt(date_raw, source_tz=source_tz)
        except Exception:
            continue

        record_id = m.group(1).strip()
        detail_url = href if href.startswith("http") else urljoin(base_url, href)

        tr = a.find_parent("tr")
        if not tr:
            continue
        tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        # Expect: [date, statutory name, title]
        issuer = tds[1] if len(tds) >= 2 else None
        title = tds[2] if len(tds) >= 3 else None
        if not issuer or not title:
            continue

        hits.append(
            ListHit(
                record_id=record_id,
                published_at_raw=date_raw,
                published_at_utc=published_at_utc,
                issuer_name_raw=issuer,
                title_raw=title,
                detail_url=detail_url,
            )
        )

    # Pagination: try rel=next in <link> tags (best if present)
    next_url: Optional[str] = None
    link_next = soup.find("link", attrs={"rel": lambda v: v and "next" in str(v).lower()})
    if link_next and link_next.get("href"):
        h = link_next["href"]
        next_url = h if h.startswith("http") else urljoin(base_url, h)
        return hits, next_url

    # Fallback: anchor with text Next/Volgende
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True).lower()
        if txt in ("next", "volgende", "›", ">"):
            h = a["href"]
            next_url = h if h.startswith("http") else urljoin(base_url, h)
            break

    return hits, next_url


def parse_detail_html(detail_html: str, *, base_url: str) -> dict:
    """
    Extract:
      - issuer (Statutory name / Statutaire naam)
      - title (Title / Titel)
      - publication date text line (Publication/Registration date)
      - related downloads (links to downloadregisterfile.aspx)
    """
    soup = BeautifulSoup(detail_html, "lxml")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def pick_after(labels: tuple[str, ...]) -> Optional[str]:
        for i, ln in enumerate(lines):
            for lab in labels:
                if ln.startswith(lab):
                    rest = ln[len(lab):].strip()
                    if rest:
                        return rest
                    if i + 1 < len(lines):
                        return lines[i + 1]
        return None

    issuer = pick_after(("Statutory name", "Statutaire naam"))
    title = pick_after(("Title", "Titel"))
    pub = pick_after(("Publication date", "Publicatie datum", "Registration date", "Registratie datum"))

    # AFM añade sufijos tipo "(opens in a new window)" al texto del link
    suffixes = (
        " (opens in a new window)",
        " (opens in a new tab)",
        " (opent in een nieuw venster)",
        " (opent in een nieuw tabblad)",
    )

    def clean_filename(name: str) -> str:
        n = name.strip()
        low = n.lower()
        for s in suffixes:
            if low.endswith(s):
                n = n[: -len(s)].strip()
                break
        return n

    downloads: list[RelatedDownload] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "downloadregisterfile.aspx" not in href.lower():
            continue

        name = clean_filename(a.get_text(" ", strip=True))
        if not name:
            name = "download.bin"

        abs_href = href if href.startswith("http") else urljoin(base_url, href)
        downloads.append(RelatedDownload(filename=name, href=abs_href))

    return {
        "issuer_name_raw": issuer,
        "title_raw": title,
        "published_at_raw": pub,
        "related_downloads": downloads,
    }


# --- Export CSV helpers (probe/QA). CSV appears flattened in viewer. :contentReference[oaicite:4]{index=4}
def _reconstruct_csv_lines(text: str) -> str:
    # Insert newline before each quoted ISO-ish datetime occurrence:
    # "YYYY-MM-DD HH:MM:SS";"Statutory name";"Title"
    return re.sub(r'"\s+"(?=\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}")', '"\n"', text)


def parse_export_csv(text: str) -> list[tuple[str, str, str]]:
    """
    Returns list of (published_at_raw, statutory_name, title).
    NOTE: Export CSV does not include detail_id, so it can't be the only discovery source. :contentReference[oaicite:5]{index=5}
    """
    import csv
    import io

    fixed = _reconstruct_csv_lines(text)
    r = csv.reader(io.StringIO(fixed), delimiter=";", quotechar='"')
    rows: list[tuple[str, str, str]] = []
    for row in r:
        if not row:
            continue
        if row[0].strip().lower() in ("publication date",):
            continue
        if len(row) >= 3 and re.match(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", row[0].strip()):
            rows.append((row[0].strip(), row[1].strip(), row[2].strip()))
    return rows