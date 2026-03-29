from zoneinfo import ZoneInfo

from oam.connectors.nl.afm_inside_info_parsers import (
    parse_publication_dt,
    parse_list_page,
    parse_detail_page,
)


def test_parse_publication_dt_en_nl():
    tz = ZoneInfo("Europe/Amsterdam")
    assert parse_publication_dt("03 mar 2026 - 08:05", source_tz=tz).isoformat().endswith("+00:00")
    assert parse_publication_dt("3 mrt 2026 - 08:05", source_tz=tz).isoformat().endswith("+00:00")
    assert parse_publication_dt("5 mei 2011 - 14:04", source_tz=tz).isoformat().endswith("+00:00")


def test_parse_list_page_extracts_rows():
    html = """
    <html><body>
      <table>
        <tr>
          <td><a href="/en/sector/registers/meldingenregisters/openbaarmaking-voorwetenschap/details?id=C2603-00103">03 mar 2026 - 08:05</a></td>
          <td>ING Groep N.V.</td>
          <td>ING completes share repurchase</td>
        </tr>
      </table>
    </body></html>
    """
    # parse_list_page returns list[ListHit], not a tuple
    hits = parse_list_page(html, base_url="https://www.afm.nl", source_tz=ZoneInfo("Europe/Amsterdam"))
    assert len(hits) == 1
    assert hits[0].record_id == "C2603-00103"
    assert hits[0].issuer_name_raw == "ING Groep N.V."
    assert hits[0].title_raw == "ING completes share repurchase"
    assert hits[0].detail_url.startswith("https://www.afm.nl/")


def test_parse_detail_page_extracts_downloads():
    html = """
    <html><body>
      <h1>ING Groep N.V.</h1>
      <div>Publication date 03 mar 2026 - 08:05</div>
      <div>Statutory name ING Groep N.V.</div>
      <div>Title ING completes share repurchase for employee compensation</div>
      <h2>Related downloads</h2>
      <a href="/downloadregisterfile.aspx?enc=XYZ&type=openbaarmaking-voorwetenschap">
  C2603-00103_ING_03032026_ENG2.pdf (opens in a new window)
</a>
    </body></html>
    """
    # parse_detail_page returns a DetailParsed dataclass (not a dict)
    d = parse_detail_page(
        html,
        detail_url="https://www.afm.nl/en/sector/registers/meldingenregisters/openbaarmaking-voorwetenschap/details?id=C2603-00103",
        base_url="https://www.afm.nl",
        source_tz=ZoneInfo("Europe/Amsterdam"),
    )
    assert d.issuer_name_raw == "ING Groep N.V."
    assert "share repurchase" in (d.title_raw or "")
    # downloads field (not related_downloads)
    downloads = d.downloads
    assert len(downloads) == 1
    assert downloads[0].filename.endswith(".pdf")
    assert downloads[0].href.startswith("https://www.afm.nl/downloadregisterfile.aspx")
