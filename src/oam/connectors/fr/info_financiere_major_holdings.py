from __future__ import annotations

from oam.connectors.registry import register
from oam.connectors.fr.info_financiere_base import FRInfoFinanciereBaseConnector


@register
class FRInfoFinanciereMajorHoldings(FRInfoFinanciereBaseConnector):
    """
    FR Bucket 4 — Major holdings / Shareholdings disclosure (Franchissements de seuil).

    Source: AMF / info-financiere.gouv.fr — flux-amf-new-prod
    Filter: sous_type_d_information = "Décision de franchissement de seuil"
    ~8,877 records.

    OPEN RISK — Historical gap: earliest record is 2017-06-16.
    Data before 2017-06-16 is NOT available in this source.
    Coverage from 2015-01-01 is PARTIAL (gap 2015-01-01 to 2017-06-15).
    This is a known limitation of the AMF public archive for this sous_type.

    NL reference: afm_substantial_holdings.py (same bucket type).
    Key difference from NL: NL scrapes HTML list pages. FR has direct API + url_de_recuperation.
    Deviation from NL: inherits FRInfoFinanciereBaseConnector (shared API pagination).
    """

    source_code = "INFOFIN_MAJOR_HOLDINGS"
    source_name = "AMF / info-financiere.gouv.fr — Franchissements de seuil"
    default_where_clause = 'sous_type_d_information="Décision de franchissement de seuil"'
    expected_filing_types = ("Décision de franchissement de seuil",)
