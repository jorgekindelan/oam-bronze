from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


DETAIL_ID_RE = re.compile(r"details\?id=([^&]+)", re.IGNORECASE)
PAGE_NUM_RE = re.compile(r"(?:[?&](?:page|p|pagina)=|/page/)(\d+)", re.IGNORECASE)
DT_RE = re.compile(r"(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3})\s+(?P<year>\d{4})", re.IGNORECASE)
ISIN_RE = re.compile(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b")
PERCENT_RE = re.compile(r"(?P<value>\d+,\d+)\s*%")

MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    # NL variants
    "mrt": 3,
    "mei": 5,
    "okt": 10,
}


@dataclass(frozen=True)
class ListHit:
    record_id: str
    published_at_raw: str
    published_at_utc: datetime
    issuer_name_raw: str
    notifier_raw: str
    detail_url: str


@dataclass(frozen=True)
class DetailParsed:
    record_id: str
    published_at_raw: str
    published_at_utc: datetime
    notifier_raw: str
    issuer_name_raw: str
    chamber_of_commerce_raw: Optional[str]
    place_of_residence_raw: Optional[str]
    capital_interest_total_pct_raw: Optional[str]
    voting_rights_total_pct_raw: Optional[str]
    isins: list[str]


def extract_record_id_from_url(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    rid = (q.get("id") or [""])[0]
    if rid:
        return rid
    m = DETAIL_ID_RE.search(url)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot extract record id from url: {url}")


def extract_page_number(url: str) -> Optional[int]:
    m = PAGE_NUM_RE.search(url)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def parse_date_only(text: str, *, source_tz: ZoneInfo) -> datetime:
    m = DT_RE.search(text.strip())
    if not m:
        raise ValueError(f"Cannot parse date from: {text!r}")
    day = int(m.group("day"))
    mon_tok = m.group("mon").lower()
    year = int(m.group("year"))
    mon = MONTH_MAP.get(mon_tok)
    if not mon:
        raise ValueError(f"Unknown month token {mon_tok!r} in {text!r}")
    dt_local = datetime(year, mon, day, 0, 0, 0, tzinfo=source_tz)
    return dt_local.astimezone(UTC)


def parse_list_page(
    html: str,
    *,
    page_url: str,
    base_url: str,
    source_tz: ZoneInfo,
    register_path_contains: str = "/substantiele-deelnemingen",
) -> tuple[list[ListHit], Optional[str]]:
    soup = BeautifulSoup(html, "lxml")
    hits: list[ListHit] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = DETAIL_ID_RE.search(href)
        if not m:
            continue

        date_raw = a.get_text(" ", strip=True)
        try:
            published_at_utc = parse_date_only(date_raw, source_tz=source_tz)
        except Exception:
            continue

        record_id = m.group(1).strip()
        detail_url = href if href.startswith("http") else urljoin(base_url, href)

        tr = a.find_parent("tr")
        if not tr:
            continue

        tds = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        issuer = tds[1] if len(tds) >= 2 else None
        notifier = tds[2] if len(tds) >= 3 else None
        if not issuer or not notifier:
            continue

        hits.append(
            ListHit(
                record_id=record_id,
                published_at_raw=date_raw,
                published_at_utc=published_at_utc,
                issuer_name_raw=issuer,
                notifier_raw=notifier,
                detail_url=detail_url,
            )
        )

    next_page_url: Optional[str] = None
    current_page = extract_page_number(page_url) or 1

    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = a.get_text(" ", strip=True).lower()
        if register_path_contains not in href.lower():
            continue
        if "details?" in href.lower():
            continue
        if "export" in href.lower():
            continue
        if txt in {"next", "volgende", ">", "›"} or a.get("rel") == ["next"]:
            next_page_url = href if href.startswith("http") else urljoin(base_url, href)
            return hits, next_page_url

    candidates: list[tuple[int, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = a.get_text(" ", strip=True)
        if register_path_contains not in href.lower():
            continue
        if "details?" in href.lower():
            continue
        if "export" in href.lower():
            continue

        abs_href = href if href.startswith("http") else urljoin(base_url, href)
        pn = extract_page_number(abs_href)
        if pn is None and txt.isdigit():
            pn = int(txt)

        if pn is not None:
            candidates.append((pn, abs_href))

    candidates = sorted(set(candidates), key=lambda x: x[0])
    for pn, href in candidates:
        if pn > current_page:
            next_page_url = href
            break

    return hits, next_page_url


def parse_detail_page(detail_html: str, *, detail_url: str, source_tz: ZoneInfo) -> DetailParsed:
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

    published_at_raw = pick_after(("Date of transaction", "Datum transactie")) or ""
    if not published_at_raw:
        raise ValueError("Cannot find 'Date of transaction' on detail page")

    notifier = pick_after(("Person obliged to notify", "Meldingsplichtige")) or ""
    issuer = pick_after(("Issuing institution", "Uitgevende instelling")) or ""
    chamber = pick_after(("Registration Chamber of Commerce", "Kamer van Koophandel"))
    place = pick_after(("Place of residence", "Plaats van vestiging"))

    # Try to extract the "Total holding" values from the collapsed percentage table text.
    capital_total = None
    voting_total = None
    for i, ln in enumerate(lines):
        if "Type Kapitaalbelang" in ln or "Type Capital interest" in ln:
            m = PERCENT_RE.search(ln)
            if m:
                capital_total = m.group("value") + " %"
        if "Type Stemrecht" in ln or "Type Voting rights" in ln:
            m = PERCENT_RE.search(ln)
            if m:
                voting_total = m.group("value") + " %"

    isins = sorted(set(ISIN_RE.findall(text)))

    return DetailParsed(
        record_id=extract_record_id_from_url(detail_url),
        published_at_raw=published_at_raw,
        published_at_utc=parse_date_only(published_at_raw, source_tz=source_tz),
        notifier_raw=notifier,
        issuer_name_raw=issuer,
        chamber_of_commerce_raw=chamber,
        place_of_residence_raw=place,
        capital_interest_total_pct_raw=capital_total,
        voting_rights_total_pct_raw=voting_total,
        isins=isins,
    )
