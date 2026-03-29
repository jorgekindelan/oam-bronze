from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# ES Bucket 3 — Inside Information / Información Privilegiada (post-08/02/2020)
#
# Source:
#   https://www.cnmv.es/portal/Informacion-Privilegiada/resultado-ip.aspx
#   ?fechaDesde=DD/MM/YYYY&fechaHasta=DD/MM/YYYY&page=N
#
# Direct GET, no session required. Pagination via &page=0, &page=1, etc.
# Page iteration stops when a page returns no results rows.
#
# For filings BEFORE 08/02/2020, see cnmv_hr.py (Hechos Relevantes).
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
    parse_cnmv_datetime,
)

logger = get_logger("oam.es.cnmv_ip")

_RESULTS_URL_TEMPLATE = (
    "https://www.cnmv.es/portal/Informacion-Privilegiada/resultado-ip.aspx"
    "?fechaDesde={date_from}&fechaHasta={date_to}&page={page}"
)
_DEFAULT_UA = "oam-bronze/0.1 (+https://github.com/jorgekindelan/oam-bronze)"

# CNMV switched from Hechos Relevantes to Información Privilegiada on this date
_IP_START_DATE_STR = "08/02/2020"


# ── HTML parser ───────────────────────────────────────────────────────────────


def parse_ip_results_page(html: str) -> list[dict[str, Optional[str]]]:
    """
    Parse the resultado-ip.aspx page.

    Each result row contains:
      fecha, hora, emisor_nombre, tipo, titulo, numero_registro, verdocumento_url

    Returns list of dicts; rows with missing fecha/numero_registro are skipped.
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, Optional[str]]] = []

    # The results live in a table or repeated <div> blocks.
    # CNMV renders each filing as a <tr> in a results table.
    table = soup.find("table", class_=lambda c: c and "resultado" in c.lower())
    if table is None:
        table = soup.find("table")
    if table is None:
        return rows

    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        # Column order (observed): fecha | hora | emisor | tipo | titulo | nreg | doc
        # Be resilient to minor layout changes by scanning for verdocumento link
        fecha_raw = tds[0].get_text(strip=True)
        hora_raw = tds[1].get_text(strip=True) if len(tds) > 1 else ""
        emisor = tds[2].get_text(strip=True) if len(tds) > 2 else None
        tipo = tds[3].get_text(strip=True) if len(tds) > 3 else None
        titulo = tds[4].get_text(strip=True) if len(tds) > 4 else None
        numero_registro = tds[5].get_text(strip=True) if len(tds) > 5 else None

        # Verdocumento link may appear anywhere in the row
        verdoc_url: Optional[str] = None
        for a in tr.find_all("a", href=True):
            url = extract_verdocumento_url(a["href"])
            if url:
                verdoc_url = url
                break

        if not fecha_raw or not numero_registro:
            continue

        rows.append(
            {
                "fecha_raw": fecha_raw,
                "hora_raw": hora_raw,
                "emisor_nombre": emisor,
                "tipo": tipo,
                "titulo": titulo,
                "numero_registro": numero_registro,
                "verdocumento_url": verdoc_url,
            }
        )
    return rows


# ── Connector ─────────────────────────────────────────────────────────────────


@register
class ESCNMVIPConnector(BaseConnector):
    """
    ES Bucket 3 — CNMV Información Privilegiada (post-08/02/2020).

    Covers inside information / price-sensitive announcements published via
    the CNMV IP portal from 08/02/2020 onwards.
    For pre-2020 Hechos Relevantes, see ESCNMVHRConnector.
    """

    country_code = "ES"
    source_code = "CNMV_IP"
    source_name = "CNMV Información Privilegiada (post-08/02/2020)"

    def checkpoint_load(self, state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if state is None:
            return {"watermark_numero_registro": None}
        state.setdefault("watermark_numero_registro", None)
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

        watermark: Optional[str] = checkpoint.get("watermark_numero_registro") if checkpoint else None
        total_yielded = 0
        page = 0

        async with build_async_client(timeout_s=timeout_s, headers=headers) as client:
            while True:
                url = _RESULTS_URL_TEMPLATE.format(
                    date_from=date_from_str, date_to=date_to_str, page=page
                )
                await self._throttle()
                r = await client.get(url)
                r.raise_for_status()

                rows = parse_ip_results_page(r.text)

                if not rows:
                    if page == 0:
                        logger.info(
                            "cnmv_ip_empty_window",
                            extra={"date_from": date_from_str, "date_to": date_to_str},
                        )
                    break

                for row in rows:
                    fecha_raw = row["fecha_raw"]
                    hora_raw = row.get("hora_raw", "")
                    numero_registro = row["numero_registro"]
                    emisor = row.get("emisor_nombre")
                    tipo = row.get("tipo")
                    titulo = row.get("titulo")
                    verdoc_url = row.get("verdocumento_url")

                    try:
                        published_at_utc = parse_cnmv_datetime(fecha_raw or "", hora_raw or "00:00")
                    except ValueError:
                        logger.warning(
                            "cnmv_ip_bad_date",
                            extra={"numero_registro": numero_registro, "fecha_raw": fecha_raw},
                        )
                        continue

                    isin = extract_isin(emisor)

                    ids = make_ids(
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_record_id_raw=numero_registro,
                        detail_url=None,
                        download_url=verdoc_url,
                    )

                    title = titulo or (
                        f"{emisor}, {tipo}" if emisor and tipo else (emisor or tipo)
                    )

                    yield DiscoveryRecord(
                        discovery_id=ids.discovery_id,
                        crawl_run_id=crawl_run_id,
                        country_code=self.country_code,
                        source_code=self.source_code,
                        source_name=self.source_name,
                        source_record_id_raw=numero_registro,
                        issuer_name_raw=emisor,
                        issuer_id_raw=None,
                        isin=isin,
                        lei=None,
                        filing_type_raw=tipo,  # preserved verbatim from CNMV
                        title_raw=title,
                        published_at_raw=f"{fecha_raw} {hora_raw}".strip(),
                        published_at_utc=published_at_utc,
                        period_end_raw=None,
                        period_end_date=None,
                        language_raw="es",
                        detail_url=None,
                        download_url=verdoc_url,
                        metadata_payload_raw={
                            "results_page_url": url,
                            "numero_registro": numero_registro,
                            "hora_raw": hora_raw,
                        },
                        discovered_at_utc=now_utc(),
                        discovery_status="DISCOVERED" if verdoc_url else "DISCOVERED_NO_DOWNLOAD_URL",
                    )

                    total_yielded += 1
                    if watermark is None or (numero_registro or "") > watermark:
                        watermark = numero_registro

                logger.info(
                    "cnmv_ip_page_fetched",
                    extra={"page": page, "rows": len(rows), "total_yielded": total_yielded},
                )
                page += 1

        if checkpoint is not None and watermark is not None:
            checkpoint["watermark_numero_registro"] = watermark

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
