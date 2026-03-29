from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# ES Bucket 4 — Major Holdings / Participaciones Significativas (PS)
#
# Source:
#   https://www.cnmv.es/portal/Consultas/MayoresParticipaciones/Busqueda.aspx
#   POST-based form search (ASP.NET WebForms with __VIEWSTATE).
#
# Two-step flow per entity window:
#   1. GET the form page to extract __VIEWSTATE, __EVENTVALIDATION.
#   2. POST with date range + entity filter to retrieve the results table.
#
# For the date-range discovery pass (no entity pre-filter), we iterate via
# the form's date-range fields with an empty entity search, relying on the
# server to return all filings within the window.  This may produce paginated
# results; page continuation is handled by re-POSTing with the "__doPostBack"
# event target for the pager control.
#
# OPEN RISK — RISK-ES-01: If CNMV requires client-side JS to generate
# __VIEWSTATE (observed on some ASP.NET pages), simple httpx POST will fail.
# In that case Playwright is needed and this connector must be marked BROKEN.
# The current implementation assumes __VIEWSTATE is present in the server-
# rendered HTML (as verified on the busquedahr equivalent).
#
# OPEN RISK — RISK-ES-03: The verdocumento e= token in download_url may expire
# between discover() and download_document().  If the download returns 404/403,
# callers should re-discover and retry.
#
# NL pattern compliance: checkpoint_load / discover (async generator) /
# download_document signatures match BaseConnector exactly.
#
# Deviation from NL: POST-based form search with VIEWSTATE (same class of
# deviation as cnmv_hr.py).  Documented here as the B4 canonical reference.
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import re
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from bs4 import BeautifulSoup

from oam.connectors.base import BaseConnector
from oam.connectors.registry import register
from oam.core.http import build_async_client
from oam.core.ids import make_ids
from oam.core.logging import get_logger
from oam.core.time import now_utc
from oam.models.discovery import DiscoveryRecord
from oam.models.document import DocumentRecord

from oam.connectors.es.cnmv_parsers import (
    CNMV_BASE,
    extract_isin,
    extract_nreg,
    extract_verdocumento_url,
    parse_cnmv_date,
)

logger = get_logger("oam.es.cnmv_ps")

_FORM_URL = "https://www.cnmv.es/portal/Consultas/MayoresParticipaciones/Busqueda.aspx"
_DEFAULT_UA = "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)"

_VIEWSTATE_RE = re.compile(r'id="__VIEWSTATE"[^>]*value="([^"]*)"')
_EVENTVALIDATION_RE = re.compile(r'id="__EVENTVALIDATION"[^>]*value="([^"]*)"')
_VIEWSTATEGENERATOR_RE = re.compile(r'id="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"')

# Pager postback target — used to navigate result pages without a fresh GET
_PAGER_TARGET = "ctl00$ContentPrincipal$gridResultados"


# ── VIEWSTATE helpers ─────────────────────────────────────────────────────────


def _extract_viewstate(html: str) -> dict[str, str]:
    vs = _VIEWSTATE_RE.search(html)
    ev = _EVENTVALIDATION_RE.search(html)
    vsg = _VIEWSTATEGENERATOR_RE.search(html)
    return {
        "__VIEWSTATE": vs.group(1) if vs else "",
        "__EVENTVALIDATION": ev.group(1) if ev else "",
        "__VIEWSTATEGENERATOR": vsg.group(1) if vsg else "",
    }


def _build_search_post_data(
    *,
    date_from_str: str,
    date_to_str: str,
    viewstate: dict[str, str],
) -> dict[str, str]:
    """Build the POST body for a fresh date-range search."""
    return {
        "__VIEWSTATE": viewstate["__VIEWSTATE"],
        "__EVENTVALIDATION": viewstate["__EVENTVALIDATION"],
        "__VIEWSTATEGENERATOR": viewstate["__VIEWSTATEGENERATOR"],
        "ctl00$ContentPrincipal$wFechas$fecha_desde": date_from_str,
        "ctl00$ContentPrincipal$wFechas$fecha_hasta": date_to_str,
        "ctl00$ContentPrincipal$btnOk": "Buscar",
    }


def _build_pager_post_data(
    *,
    page_arg: str,
    date_from_str: str,
    date_to_str: str,
    viewstate: dict[str, str],
) -> dict[str, str]:
    """Build the POST body for a pager navigation postback."""
    return {
        "__VIEWSTATE": viewstate["__VIEWSTATE"],
        "__EVENTVALIDATION": viewstate["__EVENTVALIDATION"],
        "__VIEWSTATEGENERATOR": viewstate["__VIEWSTATEGENERATOR"],
        "__EVENTTARGET": _PAGER_TARGET,
        "__EVENTARGUMENT": page_arg,
        "ctl00$ContentPrincipal$wFechas$fecha_desde": date_from_str,
        "ctl00$ContentPrincipal$wFechas$fecha_hasta": date_to_str,
    }


# ── HTML parser ───────────────────────────────────────────────────────────────


def parse_ps_results_page(html: str) -> list[dict[str, Optional[str]]]:
    """
    Parse the Participaciones Significativas results table.

    Expected column order (verified 2024):
      Fecha publicación | Declarante | Emisora | Tipo | Nº registro | Documento

    Returns list of dicts with keys:
      fecha_publicacion_raw, declarante, emisora, tipo, nreg, verdocumento_url
    Rows without fecha or nreg are skipped.
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Optional[str]]] = []

    table = soup.find("table", id=lambda i: i and "grid" in i.lower())
    if table is None:
        table = soup.find("table")
    if table is None:
        return rows

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        fecha_raw = tds[0].get_text(strip=True) or None
        declarante = tds[1].get_text(strip=True) if len(tds) > 1 else None
        emisora = tds[2].get_text(strip=True) if len(tds) > 2 else None
        tipo = tds[3].get_text(" ", strip=True) if len(tds) > 3 else None
        nreg_raw = tds[4].get_text(strip=True) if len(tds) > 4 else None

        # nreg may come from href anchor in any cell
        nreg: Optional[str] = None
        for a in tr.find_all("a", href=True):
            n = extract_nreg(a["href"])
            if n:
                nreg = n
                break
        # Fallback: use text of column 4 if it looks numeric
        if not nreg and nreg_raw and nreg_raw.isdigit():
            nreg = nreg_raw

        # Verdocumento link — scan all <a> in row
        verdoc_url: Optional[str] = None
        for a in tr.find_all("a", href=True):
            url = extract_verdocumento_url(a["href"])
            if url:
                verdoc_url = url
                break

        if not fecha_raw:
            continue

        rows.append(
            {
                "fecha_publicacion_raw": fecha_raw,
                "declarante": declarante,
                "emisora": emisora,
                "tipo": tipo,
                "nreg": nreg,
                "verdocumento_url": verdoc_url,
            }
        )
    return rows


def parse_ps_next_page_args(html: str) -> list[str]:
    """
    Extract postback page arguments from the pager row of the results table.

    Returns list of page argument strings (e.g. ["Page$2", "Page$3", ...])
    for pages not yet visited.  Returns empty list when no pager is present.
    """
    soup = BeautifulSoup(html, "lxml")
    args: list[str] = []
    # ASP.NET pager links use __doPostBack(target, argument)
    pager_re = re.compile(r"__doPostBack\s*\(\s*'[^']*'\s*,\s*'(Page\$\d+)'\s*\)")
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        m = pager_re.search(href)
        if m:
            args.append(m.group(1))
    return args


# ── Connector ─────────────────────────────────────────────────────────────────


@register
class ESCNMVPSConnector(BaseConnector):
    """
    ES Bucket 4 — CNMV Participaciones Significativas (major holdings).

    Covers shareholding threshold notifications (participaciones significativas)
    submitted to CNMV via the Mayores Participaciones portal.

    OPEN RISK — RISK-ES-01: If __VIEWSTATE is generated client-side, this
    connector will fail silently.  Monitor for empty result sets on live runs.
    OPEN RISK — RISK-ES-03: verdocumento e= tokens may expire; re-discover on
    download failure.
    """

    country_code = "ES"
    source_code = "CNMV_PS"
    source_name = "CNMV Participaciones Significativas (major holdings)"

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if state is None:
            return {"watermark_nreg": None}
        state.setdefault("watermark_nreg", None)
        return state

    async def _throttle(self) -> None:
        rps = float(self.config.get("throttle_rps", 1.0))
        if rps > 0:
            await asyncio.sleep(1.0 / rps)

    async def discover(
        self,
        *,
        crawl_run_id: str,
        date_from: datetime,
        date_to: datetime,
        checkpoint: Optional[dict[str, Any]],
    ) -> AsyncIterator[DiscoveryRecord]:
        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault("User-Agent", _DEFAULT_UA)
        timeout_s = int(self.config.get("timeout_s", 60))

        date_from_str = date_from.strftime("%d/%m/%Y")
        date_to_str = date_to.strftime("%d/%m/%Y")

        watermark: Optional[str] = checkpoint.get("watermark_nreg") if checkpoint else None
        total_yielded = 0

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            # Step 1: GET the form to obtain __VIEWSTATE
            await self._throttle()
            r_form = await client.get(_FORM_URL)
            r_form.raise_for_status()
            viewstate = _extract_viewstate(r_form.text)

            if not viewstate["__VIEWSTATE"]:
                raise RuntimeError(
                    "CNMV_PS: Could not extract __VIEWSTATE from Busqueda.aspx form. "
                    "RISK-ES-01: The form may require JS to render — consider Playwright."
                )

            # Step 2: POST the initial search
            post_data = _build_search_post_data(
                date_from_str=date_from_str,
                date_to_str=date_to_str,
                viewstate=viewstate,
            )
            await self._throttle()
            r_results = await client.post(_FORM_URL, data=post_data)
            r_results.raise_for_status()

            current_html = r_results.text
            visited_pages: set[str] = set()
            page_label = "initial"

            while True:
                rows = parse_ps_results_page(current_html)

                if not rows:
                    if page_label == "initial":
                        logger.info(
                            "cnmv_ps_empty_results",
                            extra={"date_from": date_from_str, "date_to": date_to_str},
                        )
                    break

                logger.info(
                    "cnmv_ps_page_fetched",
                    extra={"page": page_label, "rows": len(rows), "total_yielded": total_yielded},
                )

                for row in rows:
                    fecha_raw = row["fecha_publicacion_raw"]
                    nreg = row["nreg"]
                    declarante = row.get("declarante")
                    emisora = row.get("emisora")
                    tipo = row.get("tipo")
                    verdoc_url = row.get("verdocumento_url")

                    if not fecha_raw:
                        continue

                    try:
                        published_at_utc = parse_cnmv_date(fecha_raw)
                    except ValueError:
                        logger.warning(
                            "cnmv_ps_bad_date",
                            extra={"nreg": nreg, "fecha_raw": fecha_raw},
                        )
                        continue

                    if not (date_from <= published_at_utc <= date_to):
                        continue

                    isin = extract_isin(emisora)

                    # Use nreg as stable source_record_id; fallback to fecha+declarante
                    source_record_id = nreg or f"{fecha_raw}|{declarante}|{emisora}"

                    ids = make_ids(
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_record_id_raw=source_record_id,
                        detail_url=None,
                        download_url=verdoc_url,
                    )

                    title = (
                        f"{declarante} en {emisora}"
                        if declarante and emisora
                        else (declarante or emisora or source_record_id)
                    )

                    yield DiscoveryRecord(
                        discovery_id=ids.discovery_id,
                        crawl_run_id=crawl_run_id,
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_name=self.source_name,
                        source_record_id_raw=source_record_id,
                        issuer_name_raw=emisora,
                        issuer_id_raw=None,
                        isin=isin,
                        lei=None,
                        filing_type_raw=tipo,  # preserved verbatim from CNMV PS tipo
                        title_raw=title,
                        published_at_raw=fecha_raw,
                        published_at_utc=published_at_utc,
                        period_end_raw=None,
                        period_end_date=None,
                        language_raw="es",
                        detail_url=None,
                        download_url=verdoc_url,
                        metadata_payload_raw={
                            "nreg": nreg,
                            "declarante": declarante,
                            "search_date_from": date_from_str,
                            "search_date_to": date_to_str,
                        },
                        discovered_at_utc=now_utc(),
                        discovery_status="DISCOVERED" if verdoc_url else "DISCOVERED_NO_DOWNLOAD_URL",
                    )

                    total_yielded += 1
                    if nreg and (watermark is None or nreg > watermark):
                        watermark = nreg

                # Check for pager links and advance if available
                next_pages = parse_ps_next_page_args(current_html)
                unvisited = [p for p in next_pages if p not in visited_pages]
                if not unvisited:
                    break

                next_page_arg = unvisited[0]
                visited_pages.add(next_page_arg)
                page_label = next_page_arg

                # Re-extract VIEWSTATE from current page for pager POST
                pager_viewstate = _extract_viewstate(current_html)
                pager_data = _build_pager_post_data(
                    page_arg=next_page_arg,
                    date_from_str=date_from_str,
                    date_to_str=date_to_str,
                    viewstate=pager_viewstate,
                )
                await self._throttle()
                r_page = await client.post(_FORM_URL, data=pager_data)
                r_page.raise_for_status()
                current_html = r_page.text

        if checkpoint is not None and watermark is not None:
            checkpoint["watermark_nreg"] = watermark

        logger.info("cnmv_ps_discover_done", extra={"total_yielded": total_yielded})

    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        if not discovery.download_url:
            raise ValueError(
                f"Discovery {discovery.discovery_id} missing download_url (verdocumento URL). "
                "RISK-ES-03: Re-discover this record if the e= token has expired."
            )

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault("User-Agent", _DEFAULT_UA)
        timeout_s = int(self.config.get("timeout_s", 60))

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            r = await client.get(
                discovery.download_url,
                headers={"Referer": CNMV_BASE},
            )
            r.raise_for_status()

            content = r.content
            ct = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower() or None
            cd = r.headers.get("Content-Disposition")

            file_extension = "pdf"
            if ct == "application/pdf":
                file_extension = "pdf"
            elif ct in ("application/zip", "application/x-zip-compressed"):
                file_extension = "zip"
            elif ct:
                file_extension = ct.split("/")[-1] if "/" in ct else "bin"

            ids = make_ids(
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                source_record_id_raw=discovery.source_record_id_raw,
                detail_url=None,
                download_url=discovery.download_url,
            )

            doc = DocumentRecord(
                document_id=ids.document_id,
                discovery_id=discovery.discovery_id,
                crawl_run_id=crawl_run_id,
                country_code=discovery.country_code,
                source_code=discovery.source_code,
                issuer_name_raw=discovery.issuer_name_raw,
                isin=discovery.isin,
                lei=None,
                filing_type_raw=discovery.filing_type_raw,
                title_raw=discovery.title_raw,
                published_at_utc=discovery.published_at_utc,
                download_url=discovery.download_url,
                final_url=str(r.url),
                http_status=r.status_code,
                mime_type=ct,
                file_extension=file_extension,
                bytes=None,
                sha256=None,
                storage_key=None,
                downloaded_at_utc=now_utc(),
                download_status="DOWNLOADED",
                version_hint=None,
                content_disposition=cd,
                etag=r.headers.get("ETag"),
                last_modified=r.headers.get("Last-Modified"),
                error_code=None,
                error_message=None,
            )
            return doc, content
