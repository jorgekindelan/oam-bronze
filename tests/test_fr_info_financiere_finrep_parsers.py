from __future__ import annotations

from oam.connectors.fr.info_financiere_parsers import (
    extract_isin,
    extract_issuer_id,
    extract_language,
    extract_period_end,
    is_annual_report,
    parse_records,
    parse_uin_dat_amf,
)

SAMPLE_RESPONSE = {
    "total_count": 1,
    "results": [{
        "uin_idt_uin": "582412_20250514",
        "uin_dat_amf": "2025-05-14T14:27:16+00:00",
        "identificationsociete_iso_nom_soc": "AB SCIENCE",
        "identificationsociete_iso_cd_isi": "FR0010557264",
        "identificationsociete_iso_cd_lei": "969500U43TVR8CCVBJ97",
        "informationdeposee_inf_tit_inf": "Rapport Financier Annuel 2024",
        "informationdeposee_inf_lng_inf": "Français",
        "sous_type_d_information": "Rapports financiers et d'audit annuels",
        "url_de_recuperation": "https://fr.ftp.opendatasoft.com/datadila/INFOFI/432/2025/05/FC432582412_20250514.zip",
        "fichierdecontenu_inf_fic_nom": "432/2025/05/FC432582412_20250514.zip",
    }]
}


def test_parse_records_returns_search_hits():
    hits = parse_records(SAMPLE_RESPONSE)
    assert len(hits) == 1
    assert hits[0].uin_idt_uin == "582412_20250514"
    assert hits[0].issuer_name_raw == "AB SCIENCE"
    assert hits[0].isin_raw == "FR0010557264"
    assert hits[0].lei_raw == "969500U43TVR8CCVBJ97"
    assert hits[0].filing_type_raw == "Rapports financiers et d'audit annuels"
    assert hits[0].download_url == "https://fr.ftp.opendatasoft.com/datadila/INFOFI/432/2025/05/FC432582412_20250514.zip"
    assert hits[0].published_at_utc.year == 2025
    assert hits[0].published_at_utc.hour == 14  # UTC+00:00 so no conversion needed


def test_parse_records_skips_missing_uin():
    response = {
        "results": [
            {"uin_dat_amf": "2025-05-14T14:27:16+00:00"},  # missing uin_idt_uin
            {
                "uin_idt_uin": "valid_001",
                "uin_dat_amf": "2025-05-14T14:27:16+00:00",
                "sous_type_d_information": "Rapports financiers et d'audit annuels",
            },
        ]
    }
    hits = parse_records(response)
    assert len(hits) == 1
    assert hits[0].uin_idt_uin == "valid_001"


def test_extract_isin_valid():
    assert extract_isin("FR0010557264") == "FR0010557264"
    assert extract_isin("NL0000395903") == "NL0000395903"
    assert extract_isin("DE0005140008") == "DE0005140008"


def test_extract_isin_dummy_returns_none():
    assert extract_isin("999999999999") is None
    assert extract_isin(None) is None
    assert extract_isin("") is None


def test_extract_language_mapping():
    assert extract_language("Français") == "fr"
    assert extract_language("français") == "fr"
    assert extract_language("English") == "en"
    assert extract_language("anglais") == "en"
    assert extract_language("Allemand") == "de"
    assert extract_language("Néerlandais") == "nl"
    assert extract_language("unknown_lang") is None
    assert extract_language(None) is None


def test_extract_period_end_iso_date_in_title():
    raw, iso = extract_period_end("Rapport Financier Annuel 2024-12-31")
    assert raw == "2024-12-31"
    assert iso == "2024-12-31"


def test_extract_period_end_year_only_inference():
    raw, iso = extract_period_end("Rapport Financier Annuel 2024")
    assert raw == "2024"
    assert iso == "2024-12-31"


def test_extract_period_end_no_year_returns_none():
    raw, iso = extract_period_end("Rapport sans date")
    assert raw is None
    assert iso is None


def test_is_annual_report_true_and_false():
    assert is_annual_report("Rapports financiers et d'audit annuels") is True
    assert is_annual_report("  Rapports financiers et d'audit annuels  ") is True
    assert is_annual_report("Autre type") is False
    assert is_annual_report(None) is False
    assert is_annual_report("") is False


def test_parse_uin_dat_amf_utc_conversion():
    dt1 = parse_uin_dat_amf("2024-03-15T14:30:00+01:00")
    assert dt1.hour == 13
    assert dt1.tzinfo is not None

    dt2 = parse_uin_dat_amf("2024-07-20T09:00:00+02:00")
    assert dt2.hour == 7
    assert dt2.tzinfo is not None

    dt3 = parse_uin_dat_amf("2024-01-10T00:00:00+00:00")
    assert dt3.hour == 0
    assert dt3.tzinfo is not None


def test_extract_issuer_id():
    # ISIN disponible → devuelve ISIN
    assert extract_issuer_id("FR0014000MR3", "969500NMI4UP00IO8G47") == "FR0014000MR3"
    # ISIN dummy + LEI disponible → devuelve LEI con prefijo
    assert extract_issuer_id("999999999999", "969500NMI4UP00IO8G47") == "LEI:969500NMI4UP00IO8G47"
    # Solo LEI (sin ISIN en absoluto) → devuelve LEI con prefijo
    assert extract_issuer_id(None, "969500NMI4UP00IO8G47") == "LEI:969500NMI4UP00IO8G47"
    # Ni ISIN ni LEI → None
    assert extract_issuer_id("999999999999", None) is None
    assert extract_issuer_id(None, None) is None
    # ISIN dummy + LEI vacío → None
    assert extract_issuer_id("999999999999", "") is None


def test_parse_records_missing_download_url_yields_none():
    response = {
        "results": [{
            "uin_idt_uin": "no_url_001",
            "uin_dat_amf": "2025-05-14T10:00:00+00:00",
            "sous_type_d_information": "Rapports financiers et d'audit annuels",
            # url_de_recuperation deliberately absent
        }]
    }
    hits = parse_records(response)
    assert len(hits) == 1
    assert hits[0].uin_idt_uin == "no_url_001"
    assert hits[0].download_url is None
