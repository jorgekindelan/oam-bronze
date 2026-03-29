from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from oam.connectors.fr import (
    info_financiere_halfrep,
    info_financiere_inside_info,
    info_financiere_major_holdings,
    info_financiere_voting_rights,
)
from oam.connectors.fr.info_financiere_halfrep import FRInfoFinanciereHalfRep
from oam.connectors.fr.info_financiere_inside_info import FRInfoFinanciereInsideInfo
from oam.connectors.fr.info_financiere_major_holdings import FRInfoFinanciereMajorHoldings
from oam.connectors.fr.info_financiere_voting_rights import FRInfoFinanciereVotingRights
from oam.connectors.fr.info_financiere_base import FRInfoFinanciereBaseConnector
from oam.connectors.registry import get_connector
from oam.connectors.fr.info_financiere_parsers import extract_period_end, parse_records


# ── Registration tests ──────────────────────────────────────────────────────

def test_b2_registered():
    c = get_connector(country_code="FR", source_code="INFOFIN_HALFREP", config={})
    assert isinstance(c, FRInfoFinanciereHalfRep)
    assert c.country_code == "FR"


def test_b3_registered():
    c = get_connector(country_code="FR", source_code="INFOFIN_INSIDE_INFO", config={})
    assert isinstance(c, FRInfoFinanciereInsideInfo)


def test_b4_registered():
    c = get_connector(country_code="FR", source_code="INFOFIN_MAJOR_HOLDINGS", config={})
    assert isinstance(c, FRInfoFinanciereMajorHoldings)


def test_b5_registered():
    c = get_connector(country_code="FR", source_code="INFOFIN_VOTING_RIGHTS", config={})
    assert isinstance(c, FRInfoFinanciereVotingRights)


# ── Inheritance tests ────────────────────────────────────────────────────────

def test_all_inherit_base():
    for cls in [FRInfoFinanciereHalfRep, FRInfoFinanciereInsideInfo,
                FRInfoFinanciereMajorHoldings, FRInfoFinanciereVotingRights]:
        assert issubclass(cls, FRInfoFinanciereBaseConnector)


# ── Checkpoint tests ─────────────────────────────────────────────────────────

def test_checkpoint_load_none():
    c = FRInfoFinanciereHalfRep(config={})
    state = c.checkpoint_load(None)
    assert state == {"watermark_uin_dat_amf": None}


def test_checkpoint_load_existing():
    c = FRInfoFinanciereInsideInfo(config={})
    existing = {"watermark_uin_dat_amf": "2024-06-30T12:00:00+00:00"}
    state = c.checkpoint_load(existing)
    assert state["watermark_uin_dat_amf"] == "2024-06-30T12:00:00+00:00"


# ── where_clause tests ───────────────────────────────────────────────────────

def test_b2_where_clause_contains_semestriel():
    c = FRInfoFinanciereHalfRep(config={})
    assert "semestriels" in c.default_where_clause


def test_b3_where_clause_contains_privilegiees():
    c = FRInfoFinanciereInsideInfo(config={})
    assert "privilégiées" in c.default_where_clause or "privil" in c.default_where_clause


def test_b4_where_clause_contains_franchissement():
    c = FRInfoFinanciereMajorHoldings(config={})
    assert "franchissement" in c.default_where_clause.lower()


def test_b5_where_clause_contains_three_types():
    c = FRInfoFinanciereVotingRights(config={})
    wc = c.default_where_clause
    assert "droits de vote" in wc.lower()
    assert "Vie du titre" in wc or "vie du titre" in wc.lower()
    assert "Modification" in wc or "modification" in wc.lower()


# ── Bucket exclusion test (filing_type_raw isolation) ────────────────────────

HALFREP_SAMPLE = {
    "total_count": 1,
    "results": [{
        "uin_idt_uin": "HH001_20240630",
        "uin_dat_amf": "2024-07-15T10:00:00+00:00",
        "identificationsociete_iso_nom_soc": "Test Corp SA",
        "identificationsociete_iso_cd_isi": "FR0000000001",
        "identificationsociete_iso_cd_lei": None,
        "informationdeposee_inf_tit_inf": "Rapport Financier Semestriel 2024",
        "informationdeposee_inf_lng_inf": "Français",
        "sous_type_d_information": "Rapports financiers et d'audit semestriels/examens réduits",
        "url_de_recuperation": "https://fr.ftp.opendatasoft.com/datadila/INFOFI/test.pdf",
        "fichierdecontenu_inf_fic_nom": "test.pdf",
    }]
}

INSIDE_INFO_SAMPLE = {
    "total_count": 1,
    "results": [{
        "uin_idt_uin": "II001_20240315",
        "uin_dat_amf": "2024-03-15T08:30:00+00:00",
        "identificationsociete_iso_nom_soc": "Big Corp SA",
        "identificationsociete_iso_cd_isi": "FR0000000002",
        "identificationsociete_iso_cd_lei": "969500TESTLEI00001",
        "informationdeposee_inf_tit_inf": "Information privilégiée — résultats",
        "informationdeposee_inf_lng_inf": "Français",
        "sous_type_d_information": "Informations privilégiées",
        "url_de_recuperation": "https://fr.ftp.opendatasoft.com/datadila/INFOFI/test2.pdf",
        "fichierdecontenu_inf_fic_nom": "test2.pdf",
    }]
}

MAJOR_HOLDINGS_SAMPLE = {
    "total_count": 1,
    "results": [{
        "uin_idt_uin": "MH001_20220101",
        "uin_dat_amf": "2022-01-01T09:00:00+00:00",
        "identificationsociete_iso_nom_soc": "Listed Corp SA",
        "identificationsociete_iso_cd_isi": "FR0000000003",
        "identificationsociete_iso_cd_lei": None,
        "informationdeposee_inf_tit_inf": "Franchissement de seuil",
        "informationdeposee_inf_lng_inf": "Français",
        "sous_type_d_information": "Décision de franchissement de seuil",
        "url_de_recuperation": "https://fr.ftp.opendatasoft.com/datadila/INFOFI/test3.pdf",
        "fichierdecontenu_inf_fic_nom": "test3.pdf",
    }]
}

VOTING_RIGHTS_SAMPLE = {
    "total_count": 1,
    "results": [{
        "uin_idt_uin": "VR001_20240131",
        "uin_dat_amf": "2024-01-31T18:00:00+00:00",
        "identificationsociete_iso_nom_soc": "Capital Corp SA",
        "identificationsociete_iso_cd_isi": "FR0000000004",
        "identificationsociete_iso_cd_lei": None,
        "informationdeposee_inf_tit_inf": "Nombre total de droits de vote au 31/01/2024",
        "informationdeposee_inf_lng_inf": "Français",
        "sous_type_d_information": "Total du nombre de droits de vote et du capital",
        "url_de_recuperation": "https://fr.ftp.opendatasoft.com/datadila/INFOFI/test4.pdf",
        "fichierdecontenu_inf_fic_nom": "test4.pdf",
    }]
}


def test_halfrep_filing_type_raw_preserved():
    hits = parse_records(HALFREP_SAMPLE)
    assert len(hits) == 1
    assert hits[0].filing_type_raw == "Rapports financiers et d'audit semestriels/examens réduits"
    assert hits[0].filing_type_raw != "Rapports financiers et d'audit annuels"  # NOT annual


def test_inside_info_filing_type_raw_preserved():
    hits = parse_records(INSIDE_INFO_SAMPLE)
    assert len(hits) == 1
    assert hits[0].filing_type_raw == "Informations privilégiées"


def test_major_holdings_filing_type_raw_preserved():
    hits = parse_records(MAJOR_HOLDINGS_SAMPLE)
    assert len(hits) == 1
    assert hits[0].filing_type_raw == "Décision de franchissement de seuil"


def test_voting_rights_filing_type_raw_preserved():
    hits = parse_records(VOTING_RIGHTS_SAMPLE)
    assert len(hits) == 1
    assert hits[0].filing_type_raw == "Total du nombre de droits de vote et du capital"


def test_issuer_id_raw_isin_priority():
    """ISIN takes priority over LEI in issuer_id_raw."""
    from oam.connectors.fr.info_financiere_parsers import extract_issuer_id
    # Both ISIN and LEI present -> use ISIN
    assert extract_issuer_id("FR0000000002", "969500TESTLEI00001") == "FR0000000002"
    # Only LEI -> use LEI with prefix
    assert extract_issuer_id("999999999999", "969500TESTLEI00001") == "LEI:969500TESTLEI00001"
    # Neither -> None
    assert extract_issuer_id("999999999999", None) is None


# ── expected_filing_types guard tests ────────────────────────────────────────

def test_b2_expected_filing_types():
    c = FRInfoFinanciereHalfRep(config={})
    assert len(c.expected_filing_types) == 1
    assert "semestriels" in c.expected_filing_types[0]


def test_b3_expected_filing_types():
    c = FRInfoFinanciereInsideInfo(config={})
    assert c.expected_filing_types == ("Informations privilégiées",)


def test_b4_expected_filing_types():
    c = FRInfoFinanciereMajorHoldings(config={})
    assert c.expected_filing_types == ("Décision de franchissement de seuil",)


def test_b5_expected_filing_types_has_three():
    c = FRInfoFinanciereVotingRights(config={})
    assert len(c.expected_filing_types) == 3
    types_str = " ".join(c.expected_filing_types).lower()
    assert "droits de vote" in types_str
    assert "vie du titre" in types_str
    assert "modification" in types_str


# ── French date format tests ─────────────────────────────────────────────────

def test_extract_period_end_french_date_format():
    """DD/MM/YYYY format in B5 voting-rights titles must parse correctly."""
    raw, iso = extract_period_end("Nombre total de droits de vote au 31/01/2024")
    assert raw == "31/01/2024"
    assert iso == "2024-01-31"


def test_extract_period_end_french_date_not_confused_with_year_only():
    """French date must not fall through to year-only (Dec 31) fallback."""
    _, iso = extract_period_end("Total droits de vote au 28/02/2023")
    assert iso == "2023-02-28"  # NOT 2023-12-31


def test_extract_period_end_french_date_single_digit_day():
    """Single-digit day must produce zero-padded ISO date."""
    raw, iso = extract_period_end("Total droits de vote au 5/03/2022")
    assert raw == "5/03/2022"
    assert iso == "2022-03-05"


def test_extract_period_end_iso_format_unchanged():
    """Existing ISO format must still work after adding French pattern."""
    raw, iso = extract_period_end("Rapport annuel 2023-12-31")
    assert iso == "2023-12-31"


def test_extract_period_end_year_only_fallback():
    """Year-only fallback still applies when no explicit date present."""
    raw, iso = extract_period_end("Rapport financier annuel 2022")
    assert iso == "2022-12-31"


# ── Cross-bucket rejection test (async) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_b2_rejects_b1_filing_type_raw():
    """
    B2 connector must silently skip any record whose filing_type_raw is not
    in expected_filing_types — even if the record has a valid download URL.
    Simulates a cross-bucket contamination scenario.
    """
    contaminated_response = {
        "total_count": 2,
        "results": [
            {
                "uin_idt_uin": "CONTAM001",
                "uin_dat_amf": "2024-06-01T10:00:00+00:00",
                "identificationsociete_iso_nom_soc": "Rogue Corp SA",
                "identificationsociete_iso_cd_isi": "FR0000099999",
                "identificationsociete_iso_cd_lei": None,
                "informationdeposee_inf_tit_inf": "Rapport annuel 2023",
                "informationdeposee_inf_lng_inf": "Français",
                # Wrong bucket: annual instead of half-yearly
                "sous_type_d_information": "Rapports financiers et d'audit annuels",
                "url_de_recuperation": "https://fr.ftp.opendatasoft.com/test.pdf",
                "fichierdecontenu_inf_fic_nom": "test.pdf",
            },
            {
                "uin_idt_uin": "LEGIT001",
                "uin_dat_amf": "2024-06-15T10:00:00+00:00",
                "identificationsociete_iso_nom_soc": "Valid Corp SA",
                "identificationsociete_iso_cd_isi": "FR0000011111",
                "identificationsociete_iso_cd_lei": None,
                "informationdeposee_inf_tit_inf": "Rapport semestriel S1 2024",
                "informationdeposee_inf_lng_inf": "Français",
                "sous_type_d_information": "Rapports financiers et d'audit semestriels/examens réduits",
                "url_de_recuperation": "https://fr.ftp.opendatasoft.com/legit.pdf",
                "fichierdecontenu_inf_fic_nom": "legit.pdf",
            },
        ],
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=contaminated_response)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    connector = FRInfoFinanciereHalfRep(config={})
    checkpoint: dict = {"watermark_uin_dat_amf": None}

    date_from = datetime(2024, 1, 1, tzinfo=UTC)
    date_to = datetime(2024, 12, 31, tzinfo=UTC)

    with patch("oam.connectors.fr.info_financiere_base.build_async_client", return_value=mock_client):
        records = [
            r async for r in connector.discover(
                crawl_run_id="test-run-001",
                date_from=date_from,
                date_to=date_to,
                checkpoint=checkpoint,
            )
        ]

    # Only the legitimate half-yearly record should be yielded
    assert len(records) == 1
    assert records[0].source_record_id_raw == "LEGIT001"
    assert records[0].filing_type_raw == "Rapports financiers et d'audit semestriels/examens réduits"


@pytest.mark.asyncio
async def test_b5_rejects_non_b5_filing_type_raw():
    """
    B5 connector accepts all 3 of its own sous_types and rejects anything else.
    """
    response = {
        "total_count": 2,
        "results": [
            {
                "uin_idt_uin": "VR_LEGIT001",
                "uin_dat_amf": "2024-01-31T18:00:00+00:00",
                "identificationsociete_iso_nom_soc": "VR Corp SA",
                "identificationsociete_iso_cd_isi": "FR0000000004",
                "identificationsociete_iso_cd_lei": None,
                "informationdeposee_inf_tit_inf": "Nombre total de droits de vote au 31/01/2024",
                "informationdeposee_inf_lng_inf": "Français",
                "sous_type_d_information": "Total du nombre de droits de vote et du capital",
                "url_de_recuperation": "https://fr.ftp.opendatasoft.com/vr001.pdf",
                "fichierdecontenu_inf_fic_nom": "vr001.pdf",
            },
            {
                "uin_idt_uin": "VR_ROGUE002",
                "uin_dat_amf": "2024-01-31T19:00:00+00:00",
                "identificationsociete_iso_nom_soc": "Rogue Corp SA",
                "identificationsociete_iso_cd_isi": "FR0000099998",
                "identificationsociete_iso_cd_lei": None,
                "informationdeposee_inf_tit_inf": "Franchissement de seuil",
                "informationdeposee_inf_lng_inf": "Français",
                # Wrong bucket: major holdings slipped into B5 query
                "sous_type_d_information": "Décision de franchissement de seuil",
                "url_de_recuperation": "https://fr.ftp.opendatasoft.com/rogue.pdf",
                "fichierdecontenu_inf_fic_nom": "rogue.pdf",
            },
        ],
    }

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=response)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    connector = FRInfoFinanciereVotingRights(config={})
    checkpoint: dict = {"watermark_uin_dat_amf": None}

    date_from = datetime(2024, 1, 1, tzinfo=UTC)
    date_to = datetime(2024, 12, 31, tzinfo=UTC)

    with patch("oam.connectors.fr.info_financiere_base.build_async_client", return_value=mock_client):
        records = [
            r async for r in connector.discover(
                crawl_run_id="test-run-002",
                date_from=date_from,
                date_to=date_to,
                checkpoint=checkpoint,
            )
        ]

    assert len(records) == 1
    assert records[0].source_record_id_raw == "VR_LEGIT001"
