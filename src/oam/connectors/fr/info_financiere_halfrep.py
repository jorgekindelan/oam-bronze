from __future__ import annotations

from oam.connectors.registry import register
from oam.connectors.fr.info_financiere_base import FRInfoFinanciereBaseConnector


@register
class FRInfoFinanciereHalfRep(FRInfoFinanciereBaseConnector):
    """
    FR Bucket 2 — Half-yearly Financial Report (Rapport Financier Semestriel).

    Source: AMF / info-financiere.gouv.fr — flux-amf-new-prod
    Filter: sous_type_d_information = "Rapports financiers et d'audit semestriels/examens réduits"
    ~19,299 records. Historical coverage confirmed from 2015.

    NL reference: afm_finrep.py (same bucket family, periodic financial report).
    Deviation from NL: inherits FRInfoFinanciereBaseConnector (shared API pagination).
    """

    source_code = "INFOFIN_HALFREP"
    source_name = "AMF / info-financiere.gouv.fr — Rapports financiers semestriels"
    default_where_clause = (
        "sous_type_d_information=\"Rapports financiers et d'audit semestriels/examens réduits\""
    )
    expected_filing_types = ("Rapports financiers et d'audit semestriels/examens réduits",)
