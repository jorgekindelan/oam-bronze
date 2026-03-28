from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


DETAIL_ID_RE = re.compile(r"details\?id=([^&]+)", re.IGNORECASE)

DT_RE = re.compile(
    r"(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3})\s+(?P<year>\d{4})\s*-\s*(?P<h>\d{2}):(?P<m>\d{2})",
    re.IGNORECASE,
)

MONTH_MAP = {
    # EN
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # NL
    "mrt": 3, "mei": 5, "okt": 10,
}

SUFFIXES = (
    " (opens in a new window)",
    " (opens in a new tab)",
    " (opent in een nieuw venster)",
    " (opent in een nieuw tabblad)",
)

FILE_EXTS = (".pdf", ".doc", ".docx", ".zip", ".txt", ".xls", ".xlsx")


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


@dataclass(frozen=True)
class DetailParsed:
    record_id: str
    published_at_raw: str
    published_at_utc: datetime
    issuer_name_raw: str
    title_raw: str
    downloads: list[RelatedDownload]
    older_detail_url: Optional[str]
    newer_detail_url: Optional[str]


def extract_record_id_from_url(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    rid = (q.get("id") or [""])[0]
    if rid:
        return rid
    m = DETAIL_ID_RE.search(url)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot extract record id from url: {url}")


def parse_publication_dt(text: str, *, source_tz: ZoneInfo) -> datetime:
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


def parse_list_page(html: str, *, base_url: str, source_tz: ZoneInfo) -> list[ListHit]:
    soup = BeautifulSoup(html, "lxml")
    hits: list[ListHit] = []

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

    return hits


def _clean_filename(name: str) -> str:
    n = name.strip()
    low = n.lower()
    for s in SUFFIXES:
        if low.endswith(s):
            n = n[: -len(s)].strip()
            break
    return n


def _extract_downloads_regex(detail_html: str, *, base_url: str) -> list[RelatedDownload]:
    # Very robust: grab any <a ... href="...downloadregisterfile.aspx?...">TEXT</a>
    # even if the page is nested/oddly structured.
    out: list[RelatedDownload] = []
    pat = re.compile(
        r'<a[^>]+href="(?P<href>[^"]*downloadregisterfile\.aspx[^"]*)"[^>]*>(?P<txt>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for m in pat.finditer(detail_html):
        href = m.group("href")
        txt = re.sub(r"<[^>]+>", "", m.group("txt")).strip()
        name = _clean_filename(txt) or "download.bin"
        abs_href = href if href.startswith("http") else urljoin(base_url, href)
        out.append(RelatedDownload(filename=name, href=abs_href))
    return out


def parse_detail_page(detail_html: str, *, detail_url: str, base_url: str, source_tz: ZoneInfo) -> DetailParsed:
    soup = BeautifulSoup(detail_html, "lxml")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def pick_after(labels: tuple[str, ...]) -> Optional[str]:
        for i, ln in enumerate(lines):
            for lab in labels:
                if ln.startswith(lab):
                    rest = ln[len(lab) :].strip()
                    if rest:
                        return rest
                    if i + 1 < len(lines):
                        return lines[i + 1]
        return None

    pub_raw = pick_after(("Registration date", "Publication date", "Publicatie datum", "Registratie datum"))
    if not pub_raw:
        raise ValueError("Cannot find publication/registration date line in detail page")
    published_at_utc = parse_publication_dt(pub_raw, source_tz=source_tz)

    issuer = pick_after(("Statutory name", "Statutaire naam")) or ""
    title = pick_after(("Title", "Titel")) or ""

    # Downloads: soup-based first
    downloads: list[RelatedDownload] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "downloadregisterfile.aspx" not in href.lower():
            continue
        name = _clean_filename(a.get_text(" ", strip=True)) or "download.bin"
        abs_href = href if href.startswith("http") else urljoin(base_url, href)
        downloads.append(RelatedDownload(filename=name, href=abs_href))

    # Fallback: regex over raw HTML
    if not downloads:
        downloads = _extract_downloads_regex(detail_html, base_url=base_url)

    # Navigation: "Next result" / "Volgende ..." goes OLDER. :contentReference[oaicite:2]{index=2}
    older_detail_url: Optional[str] = None
    newer_detail_url: Optional[str] = None
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True).lower()
        href = a["href"]
        if "openbaarmaking-voorwetenschap/details" not in href.lower():
            continue
        abs_href = href if href.startswith("http") else urljoin(base_url, href)
        if "next result" in label or "volgende register resultaat" in label:
            older_detail_url = abs_href
        elif "previous result" in label or "vorige register resultaat" in label:
            newer_detail_url = abs_href

    record_id = extract_record_id_from_url(detail_url)

    return DetailParsed(
        record_id=record_id,
        published_at_raw=pub_raw,
        published_at_utc=published_at_utc,
        issuer_name_raw=issuer,
        title_raw=title,
        downloads=downloads,
        older_detail_url=older_detail_url,
        newer_detail_url=newer_detail_url,
    )
