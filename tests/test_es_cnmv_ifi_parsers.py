from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Tests for ES Bucket 2 (IFI) HTML parsers.
# Covers: parse_ifi_list_rows, parse_ifi_detail_verdocumento, parse_ifi_detail_period,
#         and connector registration.
# ─────────────────────────────────────────────────────────────────────────────

from oam.connectors.es.cnmv_ifi import (
    ESCNMVIFIConnector,
    parse_ifi_detail_period,
    parse_ifi_detail_verdocumento,
    parse_ifi_list_rows,
)
from oam.connectors.registry import get_connector


# ── Registration ──────────────────────────────────────────────────────────────


def test_cnmv_ifi_registered():
    c = get_connector(country_code="ES", source_code="CNMV_IFI", config={})
    assert isinstance(c, ESCNMVIFIConnector)
    assert c.country_code == "ES"
    assert c.source_code == "CNMV_IFI"


# ── parse_ifi_list_rows ───────────────────────────────────────────────────────


_IFI_LIST_HTML = """
<html><body>
  <table id="ctl00_ContentPrincipal_gridEntidades">
    <tr>
      <td><a href="../../aldia/detalleifialdia.aspx?nreg=20240001234">15/03/2024</a></td>
      <td>Telefónica S.A.</td>
      <td>II semestre de 2023-2024 individual</td>
    </tr>
    <tr>
      <td><a href="../../aldia/detalleifialdia.aspx?nreg=20240005678">10/03/2024</a></td>
      <td>Iberdrola S.A.</td>
      <td>I semestre 2024</td>
    </tr>
  </table>
</body></html>
"""


def test_parse_ifi_list_rows_extracts_rows():
    rows = parse_ifi_list_rows(_IFI_LIST_HTML)
    assert len(rows) == 2


def test_parse_ifi_list_rows_nreg():
    rows = parse_ifi_list_rows(_IFI_LIST_HTML)
    assert rows[0]["nreg"] == "20240001234"
    assert rows[1]["nreg"] == "20240005678"


def test_parse_ifi_list_rows_fecha():
    rows = parse_ifi_list_rows(_IFI_LIST_HTML)
    assert rows[0]["fecha_publicacion_raw"] == "15/03/2024"


def test_parse_ifi_list_rows_nombre_emisor():
    rows = parse_ifi_list_rows(_IFI_LIST_HTML)
    assert rows[0]["nombre_emisor"] == "Telefónica S.A."


def test_parse_ifi_list_rows_tipo():
    rows = parse_ifi_list_rows(_IFI_LIST_HTML)
    assert rows[0]["tipo"] == "II semestre de 2023-2024 individual"


def test_parse_ifi_list_rows_detail_url_populated():
    rows = parse_ifi_list_rows(_IFI_LIST_HTML)
    assert "detalleifialdia.aspx?nreg=20240001234" in rows[0]["detail_url"]


def test_parse_ifi_list_rows_empty_table():
    html = "<html><body><table id='ctl00_ContentPrincipal_gridEntidades'></table></body></html>"
    rows = parse_ifi_list_rows(html)
    assert rows == []


def test_parse_ifi_list_rows_no_table():
    rows = parse_ifi_list_rows("<html><body><p>Sin resultados</p></body></html>")
    assert rows == []


def test_parse_ifi_list_rows_skips_row_without_nreg():
    html = """
    <html><body>
      <table id="ctl00_ContentPrincipal_gridEntidades">
        <tr>
          <td><a href="../../aldia/detalleifialdia.aspx">15/03/2024</a></td>
          <td>Sin nreg</td>
          <td>Tipo</td>
        </tr>
      </table>
    </body></html>
    """
    rows = parse_ifi_list_rows(html)
    assert rows == []


# ── parse_ifi_detail_verdocumento ─────────────────────────────────────────────


_IFI_DETAIL_HTML = """
<html><body>
  <h1>Detalle IFI</h1>
  <p>Inicio período: 01/01/2023</p>
  <p>Fin período: 30/06/2023</p>
  <p>Semestre: I</p>
  <p>Ejercicio: 2023</p>
  <p>NIF: A28015406</p>
  <a href="../../webservices/verdocumento/ver?e=EncBase64TokenHere">Descargar documento</a>
</body></html>
"""


def test_parse_ifi_detail_verdocumento_extracts_url():
    url = parse_ifi_detail_verdocumento(_IFI_DETAIL_HTML)
    assert url is not None
    assert "verdocumento" in url
    assert url.startswith("https://www.cnmv.es/")


def test_parse_ifi_detail_verdocumento_none_on_missing():
    html = "<html><body><p>No document link here.</p></body></html>"
    url = parse_ifi_detail_verdocumento(html)
    assert url is None


# ── parse_ifi_detail_period ───────────────────────────────────────────────────


def test_parse_ifi_detail_period_extracts_fields():
    meta = parse_ifi_detail_period(_IFI_DETAIL_HTML)
    assert meta["inicio_periodo"] is not None
    assert "2023" in (meta["inicio_periodo"] or "")


def test_parse_ifi_detail_period_fin_periodo():
    meta = parse_ifi_detail_period(_IFI_DETAIL_HTML)
    assert meta["fin_periodo"] is not None
    assert "2023" in (meta["fin_periodo"] or "")


def test_parse_ifi_detail_period_nif():
    meta = parse_ifi_detail_period(_IFI_DETAIL_HTML)
    assert meta["nif"] is not None
    assert "A28015406" in (meta["nif"] or "")


def test_parse_ifi_detail_period_returns_none_on_empty():
    meta = parse_ifi_detail_period("<html><body></body></html>")
    assert meta["inicio_periodo"] is None
    assert meta["fin_periodo"] is None
    assert meta["nif"] is None


# ── Connector checkpoint_load ─────────────────────────────────────────────────


def test_checkpoint_load_none_returns_default():
    c = ESCNMVIFIConnector(config={})
    state = c.checkpoint_load(None)
    assert state == {"watermark_nreg": None}


def test_checkpoint_load_preserves_existing():
    c = ESCNMVIFIConnector(config={})
    state = c.checkpoint_load({"watermark_nreg": "20240001234"})
    assert state["watermark_nreg"] == "20240001234"


def test_checkpoint_load_fills_missing_key():
    c = ESCNMVIFIConnector(config={})
    state = c.checkpoint_load({"other_key": "value"})
    assert "watermark_nreg" in state
