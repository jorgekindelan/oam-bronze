from __future__ import annotations

from oam.connectors.registry import register
from oam.connectors.fr.info_financiere_base import FRInfoFinanciereBaseConnector


@register
class FRInfoFinanciereInsideInfo(FRInfoFinanciereBaseConnector):
    """
    FR Bucket 3 — Ad hoc / Inside information / Price-sensitive announcements.

    Source: AMF / info-financiere.gouv.fr — flux-amf-new-prod
    Filter: sous_type_d_information = "Informations privilégiées"
    ~199,882 records (largest bucket). Historical coverage confirmed from 2015.
    ISIN and LEI populated on most records.

    NOTE: This is the AMF French equivalent of "openbaarmaking voorwetenschap" (NL).
    NL reference: afm_inside_info.py (same bucket type).
    Key difference from NL: NL requires HTML scraping + detail page navigation.
    FR has direct url_de_recuperation in every API record — no HTML scraping needed.
    Deviation from NL: inherits FRInfoFinanciereBaseConnector (shared API pagination).
    """

    source_code = "INFOFIN_INSIDE_INFO"
    source_name = "AMF / info-financiere.gouv.fr — Informations privilégiées"
    default_where_clause = 'sous_type_d_information="Informations privilégiées"'
    expected_filing_types = ("Informations privilégiées",)
