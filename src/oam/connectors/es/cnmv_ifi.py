from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# ES Bucket 2 — Half-yearly / Quarterly Financial Reports (Informes Financieros
# Intermedios, IFI)
#
# Source:
#   List:   https://www.cnmv.es/portal/consultas/ifi/listaifi?fechaDesde=DD/MM/YYYY&fechaHasta=DD/MM/YYYY
#   Detail: https://www.cnmv.es/portal/aldia/detalleifialdia.aspx?nreg=XXXXXXXX
#
# Two-stage discovery:
#   1. GET list page → parse nreg links + basic metadata
#   2. GET detail page per nreg → extract verdocumento URL
#
# This adds N HTTP requests in discover() but produces complete DiscoveryRecords
# with populated download_url, which is required for download_document() to work
# without stateful detail-page re-fetch.
#
# NL pattern compliance: checkpoint_load / discover (async generator) /
# download_document signatures match BaseConnector exactly.
#
# Deviation from NL: CNMV uses HTML scraping (not a structured API/export).
# All CNMV connectors share this deviation; it is documented here as the
# canonical B2 reference.
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
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
    extract_period_end_from_tipo,
    extract_verdocumento_url,
    parse_cnmv_date,
)

logger = get_logger("oam.es.cnmv_ifi")

_LIST_URL_TEMPLATE = (
    "https://www.cnmv.es/portal/consultas/ifi/listaifi"
    "?fechaDesde={date_from}&fechaHasta={date_to}"
)
_DETAIL_URL_TEMPLATE = "https://www.cnmv.es/portal/aldia/detalleifialdia.aspx?nreg={nreg}"
_DEFAULT_UA = "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)"


# ── HTML parsers (package-internal, also tested via test_es_cnmv_ifi_parsers) ─


def parse_ifi_list_rows(
    html: str,
) -> list[dict[str, Optional[str]]]:
    """
    Parse the IFI list page table.

    Returns list of dicts with keys:
      nreg, fecha_publicacion_raw, nombre_emisor, tipo, detail_url
    Rows with missing nreg or date are skipped with a warning.
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Optional[str]]] = []

    table = soup.find("table", id="ctl00_ContentPrincipal_gridEntidades")
    if table is None:
        # Fallback: try any table on the page
        table = soup.find("table")
    if table is None:
        return rows

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        # First cell contains the date link href e.g. ../../aldia/detalleifialdia.aspx?nreg=XXXXXXXX
        a = tds[0].find("a", href=True)
        if a is None:
            continue

        href = a.get("href", "")
        nreg = extract_nreg(href)
        if not nreg:
            logger.warning("cnmv_ifi_list_missing_nreg", extra={"href": href})
            continue

        fecha_raw = a.get_text(strip=True)
        nombre_emisor = tds[1].get_text(strip=True) or None
        tipo = tds[2].get_text(" ", strip=True) or None

        detail_url = _DETAIL_URL_TEMPLATE.format(nreg=nreg)

        rows.append(
            {
                "nreg": nreg,
                "fecha_publicacion_raw": fecha_raw,
                "nombre_emisor": nombre_emisor,
                "tipo": tipo,
                "detail_url": detail_url,
            }
        )
    return rows


def parse_ifi_detail_verdocumento(html: str) -> Optional[str]:
    """
    Extract the first verdocumento URL from an IFI detail page.

    Returns an absolute URL or None.
    """
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        url = extract_verdocumento_url(href)
        if url:
            return url
    return None


def parse_ifi_detail_period(
    html: str,
) -> dict[str, Optional[str]]:
    """
    Extract period and entity metadata from an IFI detail page.

    Returns dict with keys:
      inicio_periodo, fin_periodo, semestre, ejercicio, nif
    All values may be None.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def pick_after(*labels: str) -> Optional[str]:
        for i, ln in enumerate(lines):
            for lab in labels:
                if ln.lower().startswith(lab.lower()):
                    rest = ln[len(lab):].strip()
                    if rest:
                        return rest
                    if i + 1 < len(lines):
                        return lines[i + 1]
        return None

    return {
        "inicio_periodo": pick_after("Inicio período", "Inicio periodo", "Inicio del período"),
        "fin_periodo": pick_after("Fin período", "Fin periodo", "Fin del período"),
        "semestre": pick_after("Semestre"),
        "ejercicio": pick_after("Ejercicio"),
        "nif": pick_after("NIF", "N.I.F."),
    }


# ── Connector ─────────────────────────────────────────────────────────────────


@register
class ESCNMVIFIConnector(BaseConnector):
    """
    ES Bucket 2 — CNMV Informes Financieros Intermedios (IFI).

    Covers half-yearly and quarterly financial reports submitted to CNMV.
    Source: CNMV IFI portal (listaifi + detalleifialdia).
    """

    country_code = "ES"
    source_code = "CNMV_IFI"
    source_name = "CNMV Informes Financieros Intermedios (IFI)"

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if state is None:
            return {"watermark_nreg": None}
        state.setdefault("watermark_nreg", None)
        return state

    async def _throttle(self) -> None:
        rps = float(self.config.get("throttle_rps", 1.5))
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
        list_url = _LIST_URL_TEMPLATE.format(date_from=date_from_str, date_to=date_to_str)

        watermark: Optional[str] = checkpoint.get("watermark_nreg") if checkpoint else None

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            await self._throttle()
            r = await client.get(list_url)
            r.raise_for_status()

            raw_list_html = r.text
            rows = parse_ifi_list_rows(raw_list_html)

            if not rows:
                logger.info(
                    "cnmv_ifi_empty_list",
                    extra={"date_from": date_from_str, "date_to": date_to_str},
                )
                return

            logger.info(
                "cnmv_ifi_list_loaded",
                extra={"rows": len(rows), "date_from": date_from_str, "date_to": date_to_str},
            )

            for row in rows:
                nreg = row["nreg"]
                fecha_raw = row["fecha_publicacion_raw"]
                detail_url = row["detail_url"]
                nombre_emisor = row["nombre_emisor"]
                tipo = row["tipo"]

                if not nreg or not fecha_raw:
                    continue

                # Validate date falls within the requested window
                try:
                    published_at_utc = parse_cnmv_date(fecha_raw)
                except ValueError:
                    logger.warning(
                        "cnmv_ifi_bad_date", extra={"nreg": nreg, "fecha_raw": fecha_raw}
                    )
                    continue

                if not (date_from <= published_at_utc <= date_to):
                    continue

                # Fetch detail page to get verdocumento URL
                await self._throttle()
                try:
                    d = await client.get(detail_url)
                    d.raise_for_status()
                except Exception as exc:
                    logger.warning(
                        "cnmv_ifi_detail_fetch_error",
                        extra={"nreg": nreg, "detail_url": detail_url, "error": str(exc)},
                    )
                    continue

                detail_html = d.text
                verdocumento_url = parse_ifi_detail_verdocumento(detail_html)
                period_meta = parse_ifi_detail_period(detail_html)

                if not verdocumento_url:
                    logger.warning(
                        "cnmv_ifi_no_verdocumento", extra={"nreg": nreg, "detail_url": detail_url}
                    )

                isin = extract_isin(nombre_emisor)
                period_end_raw, period_end_date = extract_period_end_from_tipo(tipo or "")

                ids = make_ids(
                    country_code=self.country_code,
                    source_code=self.source_code,
                    source_record_id_raw=nreg,
                    detail_url=detail_url,
                    download_url=verdocumento_url,
                )

                title = (
                    f"{nombre_emisor}, {tipo}"
                    if nombre_emisor and tipo
                    else (nombre_emisor or tipo)
                )

                yield DiscoveryRecord(
                    discovery_id=ids.discovery_id,
                    crawl_run_id=crawl_run_id,
                    country_code=self.country_code,
                    source_code=self.source_code,
                    source_name=self.source_name,
                    source_record_id_raw=nreg,
                    issuer_name_raw=nombre_emisor,
                    issuer_id_raw=period_meta.get("nif") or None,
                    isin=isin,
                    lei=None,
                    filing_type_raw=tipo,  # preserved verbatim from CNMV
                    title_raw=title,
                    published_at_raw=fecha_raw,
                    published_at_utc=published_at_utc,
                    period_end_raw=period_end_raw,
                    period_end_date=period_end_date,
                    language_raw="es",
                    detail_url=detail_url,
                    download_url=verdocumento_url,
                    metadata_payload_raw={
                        "list_url": list_url,
                        "nreg": nreg,
                        "inicio_periodo": period_meta.get("inicio_periodo"),
                        "fin_periodo": period_meta.get("fin_periodo"),
                        "semestre": period_meta.get("semestre"),
                        "ejercicio": period_meta.get("ejercicio"),
                        "nif": period_meta.get("nif"),
                    },
                    discovered_at_utc=now_utc(),
                    discovery_status="DISCOVERED" if verdocumento_url else "DISCOVERED_NO_DOWNLOAD_URL",
                )

                if watermark is None or nreg > watermark:
                    watermark = nreg

        if checkpoint is not None and watermark is not None:
            checkpoint["watermark_nreg"] = watermark

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
                headers={"Referer": discovery.detail_url or CNMV_BASE},
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
                detail_url=discovery.detail_url,
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
