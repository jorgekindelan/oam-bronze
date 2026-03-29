from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# ES Bucket 5 — Voting Rights / Capital Changes
#   (Otra Información Regulada Corporativa — Derechos de Voto y Capital)
#
# Source:
#   https://www.cnmv.es/Portal/Otra-Informacion-Regulada-Corporativa/Derechos-Voto-Capital.aspx
#   ?tipoinf=TYPE&fechaDesde=DD/MM/YYYY&fechaHasta=DD/MM/YYYY&page=N
#
#   TYPE 8: Total derechos voto y capital
#   TYPE 9: Cambios en derechos vinculados a valores
#
# Direct GET, no session required. Pagination via &page=0, &page=1, …
# Page iteration stops when a page returns no result rows.
#
# A single connector class iterates both tipoinf=8 and tipoinf=9 in sequence.
#
# NL pattern compliance: checkpoint_load / discover / download_document
# signatures match BaseConnector exactly.
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
    extract_verdocumento_url,
    parse_cnmv_date,
)

logger = get_logger("oam.es.cnmv_oirc")

_RESULTS_URL_TEMPLATE = (
    "https://www.cnmv.es/Portal/Otra-Informacion-Regulada-Corporativa/Derechos-Voto-Capital.aspx"
    "?tipoinf={tipoinf}&fechaDesde={date_from}&fechaHasta={date_to}&page={page}"
)
_DEFAULT_UA = "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)"

# Both type codes covered by this connector
_TIPO_INF_CODES = [8, 9]
_TIPO_INF_LABELS = {
    8: "Total derechos voto y capital",
    9: "Cambios en derechos vinculados a valores",
}


# ── HTML parser ───────────────────────────────────────────────────────────────


def parse_oirc_results_page(html: str) -> list[dict[str, Optional[str]]]:
    """
    Parse the Derechos-Voto-Capital results page.

    Table columns (verified 2024):
      Nombre emisor | ISIN | Capital social | Total acciones |
      Derechos voto acciones | Fecha inscripción | Total derechos voto | Documento

    Returns list of dicts with keys:
      nombre_emisor, isin_raw, fecha_inscripcion_raw, verdocumento_url
    Rows without fecha_inscripcion are skipped.
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Optional[str]]] = []

    table = soup.find("table")
    if table is None:
        return rows

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue

        nombre_emisor = tds[0].get_text(strip=True) or None
        isin_raw = tds[1].get_text(strip=True) or None

        # fecha inscripción is typically col 5 (0-indexed); scan all cells for a date
        fecha_raw: Optional[str] = None
        for td in tds:
            txt = td.get_text(strip=True)
            # Simple DD/MM/YYYY heuristic
            if len(txt) == 10 and txt[2] == "/" and txt[5] == "/":
                fecha_raw = txt
                break

        # Verdocumento link — scan all <a> in the row
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
                "nombre_emisor": nombre_emisor,
                "isin_raw": isin_raw,
                "fecha_inscripcion_raw": fecha_raw,
                "verdocumento_url": verdoc_url,
            }
        )
    return rows


# ── Connector ─────────────────────────────────────────────────────────────────


@register
class ESCNMVOIRCConnector(BaseConnector):
    """
    ES Bucket 5 — CNMV Voting Rights and Capital (tipoinf 8 and 9).

    Covers Total derechos de voto y capital (type 8) and Cambios en derechos
    vinculados a valores (type 9) from the CNMV OIRC portal.
    """

    country_code = "ES"
    source_code = "CNMV_OIRC"
    source_name = "CNMV Derechos de Voto y Capital (OIRC tipoinf 8+9)"

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if state is None:
            return {"watermark_fecha_inscripcion": None}
        state.setdefault("watermark_fecha_inscripcion", None)
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

        watermark: Optional[str] = checkpoint.get("watermark_fecha_inscripcion") if checkpoint else None
        total_yielded = 0

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            for tipoinf in _TIPO_INF_CODES:
                tipo_label = _TIPO_INF_LABELS[tipoinf]
                page = 0

                while True:
                    url = _RESULTS_URL_TEMPLATE.format(
                        tipoinf=tipoinf,
                        date_from=date_from_str,
                        date_to=date_to_str,
                        page=page,
                    )
                    await self._throttle()
                    r = await client.get(url)
                    r.raise_for_status()

                    rows = parse_oirc_results_page(r.text)

                    if not rows:
                        if page == 0:
                            logger.info(
                                "cnmv_oirc_empty_window",
                                extra={
                                    "tipoinf": tipoinf,
                                    "date_from": date_from_str,
                                    "date_to": date_to_str,
                                },
                            )
                        break

                    for row in rows:
                        nombre_emisor = row["nombre_emisor"]
                        isin_raw = row["isin_raw"]
                        fecha_raw = row["fecha_inscripcion_raw"]
                        verdoc_url = row["verdocumento_url"]

                        if not fecha_raw:
                            continue

                        try:
                            published_at_utc = parse_cnmv_date(fecha_raw)
                        except ValueError:
                            logger.warning(
                                "cnmv_oirc_bad_date",
                                extra={"fecha_raw": fecha_raw, "tipoinf": tipoinf},
                            )
                            continue

                        isin = extract_isin(isin_raw) or extract_isin(nombre_emisor)

                        # Build a stable source_record_id from tipoinf + isin + fecha
                        source_record_id = f"{tipoinf}|{isin_raw or nombre_emisor}|{fecha_raw}"

                        ids = make_ids(
                            country_code=self.country_code,
                            source_code=self.source_code,
                            source_record_id_raw=source_record_id,
                            detail_url=None,
                            download_url=verdoc_url,
                        )

                        title = (
                            f"{nombre_emisor} — {tipo_label}"
                            if nombre_emisor
                            else tipo_label
                        )

                        yield DiscoveryRecord(
                            discovery_id=ids.discovery_id,
                            crawl_run_id=crawl_run_id,
                            country_code=self.country_code,
                            source_code=self.source_code,
                            source_name=self.source_name,
                            source_record_id_raw=source_record_id,
                            issuer_name_raw=nombre_emisor,
                            issuer_id_raw=None,
                            isin=isin,
                            lei=None,
                            filing_type_raw=tipo_label,  # preserved verbatim from CNMV tipoinf label
                            title_raw=title,
                            published_at_raw=fecha_raw,
                            published_at_utc=published_at_utc,
                            period_end_raw=fecha_raw,
                            period_end_date=None,
                            language_raw="es",
                            detail_url=None,
                            download_url=verdoc_url,
                            metadata_payload_raw={
                                "results_page_url": url,
                                "tipoinf": tipoinf,
                                "isin_raw": isin_raw,
                            },
                            discovered_at_utc=now_utc(),
                            discovery_status="DISCOVERED" if verdoc_url else "DISCOVERED_NO_DOWNLOAD_URL",
                        )

                        total_yielded += 1
                        if watermark is None or fecha_raw > watermark:
                            watermark = fecha_raw

                    logger.info(
                        "cnmv_oirc_page_fetched",
                        extra={
                            "tipoinf": tipoinf,
                            "page": page,
                            "rows": len(rows),
                            "total_yielded": total_yielded,
                        },
                    )
                    page += 1

        if checkpoint is not None and watermark is not None:
            checkpoint["watermark_fecha_inscripcion"] = watermark

        logger.info("cnmv_oirc_discover_done", extra={"total_yielded": total_yielded})

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
