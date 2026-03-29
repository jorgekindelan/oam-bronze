from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Tests for ES Bucket 4 (PS) HTML parsers.
# Covers: parse_ps_results_page, parse_ps_next_page_args,
#         VIEWSTATE helpers, and connector registration.
# ─────────────────────────────────────────────────────────────────────────────

from oam.connectors.es.cnmv_ps import (
    ESCNMVPSConnector,
    _build_pager_post_data,
    _build_search_post_data,
    _extract_viewstate,
    parse_ps_next_page_args,
    parse_ps_results_page,
)
from oam.connectors.registry import get_connector


# ── Registration ──────────────────────────────────────────────────────────────


def test_cnmv_ps_registered():
    c = get_connector(country_code="ES", source_code="CNMV_PS", config={})
    assert isinstance(c, ESCNMVPSConnector)
    assert c.country_code == "ES"
    assert c.source_code == "CNMV_PS"


# ── _extract_viewstate ────────────────────────────────────────────────────────


_FORM_HTML = """
<html><body>
  <form>
    <input type="hidden" id="__VIEWSTATE" name="__VIEWSTATE" value="abc123viewstate==" />
    <input type="hidden" id="__EVENTVALIDATION" name="__EVENTVALIDATION" value="ev456==" />
    <input type="hidden" id="__VIEWSTATEGENERATOR" name="__VIEWSTATEGENERATOR" value="DEADBEEF" />
  </form>
</body></html>
"""


def test_extract_viewstate_all_fields():
    vs = _extract_viewstate(_FORM_HTML)
    assert vs["__VIEWSTATE"] == "abc123viewstate=="
    assert vs["__EVENTVALIDATION"] == "ev456=="
    assert vs["__VIEWSTATEGENERATOR"] == "DEADBEEF"


def test_extract_viewstate_empty_on_missing():
    vs = _extract_viewstate("<html><body><form></form></body></html>")
    assert vs["__VIEWSTATE"] == ""
    assert vs["__EVENTVALIDATION"] == ""
    assert vs["__VIEWSTATEGENERATOR"] == ""


# ── _build_search_post_data ───────────────────────────────────────────────────


def test_build_search_post_data_contains_dates():
    viewstate = {"__VIEWSTATE": "VS", "__EVENTVALIDATION": "EV", "__VIEWSTATEGENERATOR": "GEN"}
    data = _build_search_post_data(
        date_from_str="01/01/2023",
        date_to_str="31/12/2023",
        viewstate=viewstate,
    )
    assert data["ctl00$ContentPrincipal$wFechas$fecha_desde"] == "01/01/2023"
    assert data["ctl00$ContentPrincipal$wFechas$fecha_hasta"] == "31/12/2023"
    assert data["__VIEWSTATE"] == "VS"
    assert data["ctl00$ContentPrincipal$btnOk"] == "Buscar"


# ── _build_pager_post_data ────────────────────────────────────────────────────


def test_build_pager_post_data_sets_eventtarget():
    viewstate = {"__VIEWSTATE": "VS2", "__EVENTVALIDATION": "EV2", "__VIEWSTATEGENERATOR": "GEN2"}
    data = _build_pager_post_data(
        page_arg="Page$2",
        date_from_str="01/01/2023",
        date_to_str="31/12/2023",
        viewstate=viewstate,
    )
    assert data["__EVENTTARGET"] == "ctl00$ContentPrincipal$gridResultados"
    assert data["__EVENTARGUMENT"] == "Page$2"
    assert data["__VIEWSTATE"] == "VS2"


# ── parse_ps_results_page ─────────────────────────────────────────────────────


_PS_RESULTS_HTML = """
<html><body>
  <table id="ctl00_ContentPrincipal_gridResultados">
    <tr>
      <td>15/03/2024</td>
      <td>BlackRock Inc.</td>
      <td>Telefónica S.A.</td>
      <td>Adquisición indirecta</td>
      <td><a href="../../Otras/verDocumento.aspx?nreg=20240012345">20240012345</a></td>
      <td><a href="../../webservices/verdocumento/ver?e=EncToken123">PDF</a></td>
    </tr>
    <tr>
      <td>10/02/2024</td>
      <td>Amundi Asset Management</td>
      <td>Iberdrola S.A.</td>
      <td>Venta indirecta</td>
      <td>20240009876</td>
      <td><a href="verdocumento/ver?t=550e8400-e29b-41d4-a716-446655440000">PDF</a></td>
    </tr>
  </table>
</body></html>
"""


def test_parse_ps_results_page_extracts_rows():
    rows = parse_ps_results_page(_PS_RESULTS_HTML)
    assert len(rows) == 2


def test_parse_ps_results_page_fecha():
    rows = parse_ps_results_page(_PS_RESULTS_HTML)
    assert rows[0]["fecha_publicacion_raw"] == "15/03/2024"
    assert rows[1]["fecha_publicacion_raw"] == "10/02/2024"


def test_parse_ps_results_page_declarante():
    rows = parse_ps_results_page(_PS_RESULTS_HTML)
    assert rows[0]["declarante"] == "BlackRock Inc."


def test_parse_ps_results_page_emisora():
    rows = parse_ps_results_page(_PS_RESULTS_HTML)
    assert rows[0]["emisora"] == "Telefónica S.A."


def test_parse_ps_results_page_tipo():
    rows = parse_ps_results_page(_PS_RESULTS_HTML)
    assert rows[0]["tipo"] == "Adquisición indirecta"


def test_parse_ps_results_page_nreg_from_href():
    rows = parse_ps_results_page(_PS_RESULTS_HTML)
    # nreg extracted from <a href="...?nreg=20240012345">
    assert rows[0]["nreg"] == "20240012345"


def test_parse_ps_results_page_nreg_from_text():
    rows = parse_ps_results_page(_PS_RESULTS_HTML)
    # nreg from plain text cell (no href nreg)
    assert rows[1]["nreg"] == "20240009876"


def test_parse_ps_results_page_verdocumento_e_form():
    rows = parse_ps_results_page(_PS_RESULTS_HTML)
    assert rows[0]["verdocumento_url"] is not None
    assert "verdocumento" in rows[0]["verdocumento_url"]


def test_parse_ps_results_page_verdocumento_t_form():
    rows = parse_ps_results_page(_PS_RESULTS_HTML)
    assert rows[1]["verdocumento_url"] is not None
    assert "verdocumento" in rows[1]["verdocumento_url"]


def test_parse_ps_results_page_empty():
    html = "<html><body><table id='ctl00_ContentPrincipal_gridResultados'></table></body></html>"
    rows = parse_ps_results_page(html)
    assert rows == []


def test_parse_ps_results_page_no_table():
    rows = parse_ps_results_page("<html><body><p>Sin resultados</p></body></html>")
    assert rows == []


def test_parse_ps_results_page_skips_row_without_fecha():
    html = """
    <html><body>
      <table id="ctl00_ContentPrincipal_gridResultados">
        <tr>
          <td></td>
          <td>Declarante</td>
          <td>Emisora</td>
        </tr>
      </table>
    </body></html>
    """
    rows = parse_ps_results_page(html)
    assert rows == []


# ── parse_ps_next_page_args ───────────────────────────────────────────────────


_PAGER_HTML = """
<html><body>
  <table>
    <tr>
      <td><a href="javascript:__doPostBack('ctl00$ContentPrincipal$gridResultados','Page$2')">2</a></td>
      <td><a href="javascript:__doPostBack('ctl00$ContentPrincipal$gridResultados','Page$3')">3</a></td>
      <td><a href="javascript:__doPostBack('ctl00$ContentPrincipal$gridResultados','Page$4')">4</a></td>
    </tr>
  </table>
</body></html>
"""


def test_parse_ps_next_page_args_returns_pages():
    args = parse_ps_next_page_args(_PAGER_HTML)
    assert "Page$2" in args
    assert "Page$3" in args
    assert "Page$4" in args


def test_parse_ps_next_page_args_empty_on_no_pager():
    html = "<html><body><table><tr><td>Data</td></tr></table></body></html>"
    args = parse_ps_next_page_args(html)
    assert args == []


# ── Connector checkpoint_load ─────────────────────────────────────────────────


def test_checkpoint_load_none_returns_default():
    c = ESCNMVPSConnector(config={})
    state = c.checkpoint_load(None)
    assert state == {"watermark_nreg": None}


def test_checkpoint_load_preserves_existing():
    c = ESCNMVPSConnector(config={})
    state = c.checkpoint_load({"watermark_nreg": "20240012345"})
    assert state["watermark_nreg"] == "20240012345"


def test_checkpoint_load_fills_missing_key():
    c = ESCNMVPSConnector(config={})
    state = c.checkpoint_load({"other_key": "val"})
    assert "watermark_nreg" in state
