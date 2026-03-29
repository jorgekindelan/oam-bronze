from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Tests for shared CNMV parsing utilities (cnmv_parsers.py).
# Covers: date parsing, identifier extraction, URL normalisation, period logic.
# ─────────────────────────────────────────────────────────────────────────────

import pytest
from datetime import UTC, datetime

from oam.connectors.es.cnmv_parsers import (
    extract_isin,
    extract_nreg,
    extract_period_end_from_tipo,
    extract_verdocumento_url,
    is_valid_nif,
    parse_cnmv_date,
    parse_cnmv_datetime,
)


# ── parse_cnmv_date ───────────────────────────────────────────────────────────


def test_parse_cnmv_date_basic():
    dt = parse_cnmv_date("15/03/2023")
    assert dt == datetime(2023, 3, 15, 0, 0, 0, tzinfo=UTC)


def test_parse_cnmv_date_with_time():
    dt = parse_cnmv_date("07/11/2021 09:45")
    assert dt == datetime(2021, 11, 7, 9, 45, 0, tzinfo=UTC)


def test_parse_cnmv_date_with_seconds():
    dt = parse_cnmv_date("01/01/2020 14:00:30")
    assert dt == datetime(2020, 1, 1, 14, 0, 30, tzinfo=UTC)


def test_parse_cnmv_date_strips_whitespace():
    dt = parse_cnmv_date("  31/12/2019  ")
    assert dt.year == 2019
    assert dt.month == 12
    assert dt.day == 31


def test_parse_cnmv_date_invalid_raises():
    with pytest.raises(ValueError):
        parse_cnmv_date("2023-03-15")


def test_parse_cnmv_date_invalid_format_raises():
    with pytest.raises(ValueError):
        parse_cnmv_date("not-a-date")


# ── parse_cnmv_datetime ───────────────────────────────────────────────────────


def test_parse_cnmv_datetime_combines_fields():
    dt = parse_cnmv_datetime("22/06/2022", "11:30")
    assert dt == datetime(2022, 6, 22, 11, 30, 0, tzinfo=UTC)


def test_parse_cnmv_datetime_strips_whitespace():
    dt = parse_cnmv_datetime("  05/09/2018  ", "  08:00  ")
    assert dt.year == 2018


# ── extract_isin ──────────────────────────────────────────────────────────────


def test_extract_isin_present():
    assert extract_isin("ES0109067019 Telefónica") == "ES0109067019"


def test_extract_isin_uppercase_normalise():
    assert extract_isin("es0109067019") == "ES0109067019"


def test_extract_isin_none_on_missing():
    assert extract_isin("Banco Santander S.A.") is None


def test_extract_isin_none_on_empty():
    assert extract_isin("") is None
    assert extract_isin(None) is None


# ── extract_nreg ──────────────────────────────────────────────────────────────


def test_extract_nreg_from_href():
    href = "../../aldia/detalleifialdia.aspx?nreg=20240012345"
    assert extract_nreg(href) == "20240012345"


def test_extract_nreg_from_absolute_url():
    url = "https://www.cnmv.es/portal/aldia/detalleifialdia.aspx?nreg=99887766"
    assert extract_nreg(url) == "99887766"


def test_extract_nreg_none_on_missing():
    assert extract_nreg("https://www.cnmv.es/portal/Inicio.aspx") is None


# ── extract_verdocumento_url ──────────────────────────────────────────────────


def test_extract_verdocumento_url_relative_e_form():
    href = "../../webservices/verdocumento/ver?e=ABCDEncoded123"
    url = extract_verdocumento_url(href)
    assert url is not None
    assert url.startswith("https://www.cnmv.es/")
    assert "verdocumento" in url


def test_extract_verdocumento_url_relative_t_form():
    href = "verdocumento/ver?t=550e8400-e29b-41d4-a716-446655440000"
    url = extract_verdocumento_url(href)
    assert url is not None
    assert "verdocumento" in url


def test_extract_verdocumento_url_absolute_passthrough():
    href = "https://www.cnmv.es/webservices/verdocumento/ver?e=XYZ"
    url = extract_verdocumento_url(href)
    assert url == href


def test_extract_verdocumento_url_none_on_non_verdoc():
    assert extract_verdocumento_url("https://www.cnmv.es/portal/Inicio.aspx") is None
    assert extract_verdocumento_url("") is None
    assert extract_verdocumento_url(None) is None


def test_extract_verdocumento_url_dot_traversal_stripped():
    href = "../../verdocumento/ver?e=TEST"
    url = extract_verdocumento_url(href)
    assert url is not None
    assert "../../" not in url
    assert url.startswith("https://www.cnmv.es/")


# ── is_valid_nif ──────────────────────────────────────────────────────────────


def test_is_valid_nif_natural_person():
    assert is_valid_nif("12345678A") is True


def test_is_valid_nif_legal_entity():
    assert is_valid_nif("A1234567B") is True


def test_is_valid_nif_lowercase_accepted():
    assert is_valid_nif("12345678a") is True


def test_is_valid_nif_invalid():
    assert is_valid_nif("NOTANIF") is False
    assert is_valid_nif("") is False


# ── extract_period_end_from_tipo ──────────────────────────────────────────────


def test_period_end_first_semester():
    raw, iso = extract_period_end_from_tipo("I semestre 2024")
    assert raw == "2024"
    assert iso == "2024-06-30"


def test_period_end_second_semester():
    raw, iso = extract_period_end_from_tipo("II semestre de 2022-2023 individual")
    assert raw == "2022-2023"
    assert iso == "2023-12-31"


def test_period_end_annual():
    raw, iso = extract_period_end_from_tipo("Informe anual 2023")
    assert raw == "2023"
    assert iso == "2023-12-31"


def test_period_end_quarter_explicit():
    raw, iso = extract_period_end_from_tipo("3T 2024")
    assert raw == "2024"
    assert iso == "2024-09-30"


def test_period_end_none_on_empty():
    raw, iso = extract_period_end_from_tipo("")
    assert raw is None
    assert iso is None


def test_period_end_none_on_unrecognised():
    raw, iso = extract_period_end_from_tipo("Otro tipo de informe sin periodo")
    assert raw is None
    assert iso is None
