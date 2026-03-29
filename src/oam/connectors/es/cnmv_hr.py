from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# ES Bucket 3 — Hechos Relevantes (pre-08/02/2020 ONLY)
#
# Source:
#   https://www.cnmv.es/portal/hr/busquedahr.aspx?division=3
#   POST-based form search (ASP.NET WebForms with __VIEWSTATE).
#
# IMPORTANT: This connector covers filings BEFORE 08/02/2020 only.
# For post-2020 Información Privilegiada, see cnmv_ip.py.
#
# OPEN RISK — RISK-ES-03: The HR tipo taxonomy has ~60+ checkbox categories.
# The current implementation submits ALL relevant division-3 B3 tipo checkboxes.
# The exact checkbox index mapping was determined from the busquedahr form HTML
# observed in 2024 and may shift if CNMV restructures the form.
# TODO: Verify tipo checkbox indices against a fresh form GET before each backfill.
#
# Session management: The __VIEWSTATE must be extracted from the form GET
# before the search POST. A single httpx.AsyncClient is used throughout to
# maintain ASPSESSIONID cookies.
#
# NL pattern compliance: checkpoint_load / discover / download_document
# signatures match BaseConnector exactly.
#
# Deviation from NL: POST-based form search (stateful session).
# All CNMV connectors share the HTML scraping deviation documented in cnmv_ifi.py.
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import re
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import urljoin

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

logger = get_logger("oam.es.cnmv_hr")

_FORM_URL = "https://www.cnmv.es/portal/hr/busquedahr.aspx?division=3"
_DEFAULT_UA = "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)"

# Date boundary: HR is only relevant before this date
_HR_MAX_DATE_STR = "08/02/2020"
_HR_MAX_DATE = datetime(2020, 2, 8, 0, 0, 0)

# ── B3-relevant HR tipo checkboxes (division 3) ───────────────────────────────
# TODO: Verify these indices against a live form GET before each backfill run.
# The indices below correspond to the observed form HTML in 2024.
# Indices are 0-based offset within the lstTipos2 checkbox list.
_HR_TIPO_INDICES: list[int] = [
    40,  # Información sobre resultados
    46,  # Información financiera intermedia
    49,  # Resolución de procesos judiciales o administrativos
    51,  # Otros sobre negocio y situación financiera
    55,  # Transmisiones y adquisiciones de participaciones societarias
]

_VIEWSTATE_RE = re.compile(r'id="__VIEWSTATE"[^>]*value="([^"]*)"')
_EVENTVALIDATION_RE = re.compile(r'id="__EVENTVALIDATION"[^>]*value="([^"]*)"')
_VIEWSTATEGENERATOR_RE = re.compile(r'id="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"')


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
    data: dict[str, str] = {
        "__VIEWSTATE": viewstate["__VIEWSTATE"],
        "__EVENTVALIDATION": viewstate["__EVENTVALIDATION"],
        "__VIEWSTATEGENERATOR": viewstate["__VIEWSTATEGENERATOR"],
        "ctl00$ContentPrincipal$wFechas$fecha_desde": date_from_str,
        "ctl00$ContentPrincipal$wFechas$fecha_hasta": date_to_str,
        "ctl00$ContentPrincipal$btnOk": "Buscar",
    }
    # Tipo checkboxes (each "on" activates that tipo)
    for idx in _HR_TIPO_INDICES:
        data[f"ctl00$ContentPrincipal$wTipoEntidad$lstTipos2${idx}"] = "on"
    return data


def parse_hr_results_page(html: str) -> list[dict[str, Optional[str]]]:
    """
    Parse the HR busquedahr results table.

    Returns list of dicts with keys:
      fecha_raw, nombre_emisor, tipo, titulo, nreg, verdocumento_url
    Rows without fecha or nreg are skipped.
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Optional[str]]] = []

    # The results table varies by CNMV page version; be resilient
    table = soup.find("table", id=lambda i: i and "grid" in i.lower())
    if table is None:
        table = soup.find("table")
    if table is None:
        return rows

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        fecha_raw: Optional[str] = None
        nreg: Optional[str] = None
        verdoc_url: Optional[str] = None

        # Try to find date link (href with nreg)
        a = tds[0].find("a", href=True)
        if a:
            href = a.get("href", "")
            fecha_raw = a.get_text(strip=True)
            nreg = extract_nreg(href)

        if not fecha_raw:
            fecha_raw = tds[0].get_text(strip=True)
        if not nreg:
            # Try to find nreg in any link in the row
            for a2 in tr.find_all("a", href=True):
                n = extract_nreg(a2.get("href", ""))
                if n:
                    nreg = n
                    break

        # Verdocumento link
        for a3 in tr.find_all("a", href=True):
            url = extract_verdocumento_url(a3["href"])
            if url:
                verdoc_url = url
                break

        emisor = tds[1].get_text(strip=True) if len(tds) > 1 else None
        tipo = tds[2].get_text(" ", strip=True) if len(tds) > 2 else None
        titulo = tds[3].get_text(strip=True) if len(tds) > 3 else None

        if not fecha_raw:
            continue

        rows.append(
            {
                "fecha_raw": fecha_raw,
                "nreg": nreg,
                "nombre_emisor": emisor,
                "tipo": tipo,
                "titulo": titulo,
                "verdocumento_url": verdoc_url,
            }
        )
    return rows


@register
class ESCNMVHRConnector(BaseConnector):
    """
    ES Bucket 3 — CNMV Hechos Relevantes (pre-08/02/2020 ONLY).

    Covers B3 inside information / price-sensitive announcements from the
    legacy HR system before CNMV migrated to Información Privilegiada.

    OPEN RISK — RISK-ES-03: The tipo checkbox selection is not finalized.
    The current selection covers the most common B3-relevant categories.
    A full HR taxonomy review is deferred to Phase 2.
    """

    country_code = "ES"
    source_code = "CNMV_HR"
    source_name = "CNMV Hechos Relevantes (pre-08/02/2020)"

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if state is None:
            return {"watermark_fecha": None}
        state.setdefault("watermark_fecha", None)
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
        # Clamp date_to to HR boundary
        if date_to > _HR_MAX_DATE:
            from datetime import timezone
            date_to = _HR_MAX_DATE.replace(tzinfo=timezone.utc)

        if date_from >= _HR_MAX_DATE:
            logger.info(
                "cnmv_hr_window_after_cutoff",
                extra={"date_from": date_from.isoformat(), "cutoff": _HR_MAX_DATE_STR},
            )
            return

        headers = dict(self.config.get("headers", {}) or {})
        headers.setdefault("User-Agent", _DEFAULT_UA)
        timeout_s = int(self.config.get("timeout_s", 60))

        date_from_str = date_from.strftime("%d/%m/%Y")
        date_to_str = date_to.strftime("%d/%m/%Y")

        watermark: Optional[str] = checkpoint.get("watermark_fecha") if checkpoint else None
        total_yielded = 0

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            # Step 1: GET the form to obtain __VIEWSTATE
            await self._throttle()
            r_form = await client.get(_FORM_URL)
            r_form.raise_for_status()
            viewstate = _extract_viewstate(r_form.text)

            if not viewstate["__VIEWSTATE"]:
                raise RuntimeError(
                    "CNMV_HR: Could not extract __VIEWSTATE from busquedahr form. "
                    "CNMV may have changed the page structure."
                )

            # Step 2: POST the search
            post_data = _build_search_post_data(
                date_from_str=date_from_str,
                date_to_str=date_to_str,
                viewstate=viewstate,
            )
            await self._throttle()
            r_results = await client.post(_FORM_URL, data=post_data)
            r_results.raise_for_status()

            rows = parse_hr_results_page(r_results.text)

            if not rows:
                logger.info(
                    "cnmv_hr_empty_results",
                    extra={"date_from": date_from_str, "date_to": date_to_str},
                )
                return

            logger.info(
                "cnmv_hr_results_loaded",
                extra={"rows": len(rows), "date_from": date_from_str, "date_to": date_to_str},
            )

            for row in rows:
                fecha_raw = row["fecha_raw"]
                nreg = row["nreg"]
                emisor = row.get("nombre_emisor")
                tipo = row.get("tipo")
                titulo = row.get("titulo")
                verdoc_url = row.get("verdocumento_url")

                if not fecha_raw:
                    continue

                try:
                    published_at_utc = parse_cnmv_date(fecha_raw)
                except ValueError:
                    logger.warning(
                        "cnmv_hr_bad_date", extra={"nreg": nreg, "fecha_raw": fecha_raw}
                    )
                    continue

                if not (date_from <= published_at_utc <= date_to):
                    continue

                isin = extract_isin(emisor)

                # Use nreg as stable source_record_id if available, else fecha+emisor hash
                source_record_id = nreg or f"{fecha_raw}|{emisor}"

                ids = make_ids(
                    country_code=self.country_code,
                    source_code=self.source_code,
                    source_record_id_raw=source_record_id,
                    detail_url=None,
                    download_url=verdoc_url,
                )

                title = titulo or (f"{emisor}, {tipo}" if emisor and tipo else (emisor or tipo))

                yield DiscoveryRecord(
                    discovery_id=ids.discovery_id,
                    crawl_run_id=crawl_run_id,
                    country_code=self.country_code,
                    source_code=self.source_code,
                    source_name=self.source_name,
                    source_record_id_raw=source_record_id,
                    issuer_name_raw=emisor,
                    issuer_id_raw=None,
                    isin=isin,
                    lei=None,
                    filing_type_raw=tipo,  # preserved verbatim from CNMV HR taxonomy
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
                        "search_date_from": date_from_str,
                        "search_date_to": date_to_str,
                    },
                    discovered_at_utc=now_utc(),
                    discovery_status="DISCOVERED" if verdoc_url else "DISCOVERED_NO_DOWNLOAD_URL",
                )

                total_yielded += 1
                if watermark is None or fecha_raw > watermark:
                    watermark = fecha_raw

        if checkpoint is not None and watermark is not None:
            checkpoint["watermark_fecha"] = watermark

        logger.info("cnmv_hr_discover_done", extra={"total_yielded": total_yielded})

    async def download_document(
        self, *, crawl_run_id: str, discovery: DiscoveryRecord
    ) -> tuple[DocumentRecord, bytes]:
        if not discovery.download_url:
            raise ValueError(
                f"Discovery {discovery.discovery_id} missing download_url (verdocumento URL)"
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
