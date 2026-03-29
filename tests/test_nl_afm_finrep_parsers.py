from __future__ import annotations

from zoneinfo import ZoneInfo

from oam.connectors.nl.afm_finrep_parsers import (
    extract_isin,
    extract_language,
    extract_lei,
    extract_period_end,
    iter_export_hits,
    parse_detail_html,
)

def test_iter_export_hits_parses_headers():
    sample = (
        "A2510-03665 3/2/2026 11:10:28 AM RHI Magnesita N.V. 2025 "
        "724500UWG6A61XNA3Y36-2025-12-31-1-en-A2510-03665.zip Jaarlijkse financiële verslaggeving\n"
        "A2510-03620 2/26/2026 12:28:57 PM ING Groep N.V. 2025 ing-2025-12-31-en-A2510-03620.xbri\n"
    )
    hits = iter_export_hits(sample, source_tz=ZoneInfo("Europe/Amsterdam"))
    assert len(hits) == 2
    assert hits[0].record_id == "A2510-03665"
    assert hits[1].record_id == "A2510-03620"

def test_parse_detail_html_extracts_download_url():
    html = """
    <html><body>
      <div>Issuing institution ING Groep N.V.</div>
      <div>Reporting year 2025</div>
      <div>Type of document Jaarlijkse financiële verslaggeving Document
        <a href="/downloadregisterfile.aspx?enc=XYZ&type=financiele-verslaggeving">
          724500UWG6A61XNA3Y36-2025-12-31-1-en-A2510-03665.zip
        </a>
      </div>
    </body></html>
    """
    d = parse_detail_html(html)
    assert d["issuer_name_raw"] == "ING Groep N.V."
    assert d["reporting_year_raw"] == "2025"
    assert d["download_url"].startswith("https://www.afm.nl/downloadregisterfile.aspx")

def test_extract_lei_language_period_end():
    filename = "724500UWG6A61XNA3Y36-2025-12-31-1-en-A2510-03665.zip"
    assert extract_lei(filename) == "724500UWG6A61XNA3Y36"
    assert extract_language(filename) == "en"
    raw, iso = extract_period_end(filename)
    assert iso == "2025-12-31"
    assert extract_isin("NL0000395903") == "NL0000395903"