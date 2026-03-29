from __future__ import annotations

from oam.connectors.registry import register
from oam.connectors.fr.info_financiere_base import FRInfoFinanciereBaseConnector

# B5 combines three related sous_types into a single ODSQL OR clause.
# All three relate to voting rights, capital changes, and changes in securities rights.
_B5_WHERE = (
    'sous_type_d_information="Total du nombre de droits de vote et du capital"'
    ' OR sous_type_d_information="Vie du titre (modifications des droits attach\u00e9s aux actions, \u2026)"'
    ' OR sous_type_d_information="Modification des droits attach\u00e9s aux cat\u00e9gories d\'actions"'
)


@register
class FRInfoFinanciereVotingRights(FRInfoFinanciereBaseConnector):
    """
    FR Bucket 5 — Voting rights / Capital changes / Changes in rights.

    Source: AMF / info-financiere.gouv.fr — flux-amf-new-prod
    Three sous_types combined (OR clause):
      - "Total du nombre de droits de vote et du capital" (~64,150 records) — monthly disclosure
      - "Vie du titre (modifications des droits attachés aux actions, …)" (~1,149 records)
      - "Modification des droits attachés aux catégories d'actions" (~56 records)
    Total ~65,355 records. Historical coverage confirmed from 2015.

    filing_type_raw is preserved verbatim per record (three possible values).

    NL reference: afm_issued_capital.py (same bucket type).
    Key difference from NL: NL scrapes HTML list pages. FR has direct API + url_de_recuperation.
    Deviation from NL: inherits FRInfoFinanciereBaseConnector (shared API pagination).
    """

    source_code = "INFOFIN_VOTING_RIGHTS"
    source_name = "AMF / info-financiere.gouv.fr — Droits de vote et capital"
    default_where_clause = _B5_WHERE
    expected_filing_types = (
        "Total du nombre de droits de vote et du capital",
        "Vie du titre (modifications des droits attachés aux actions, \u2026)",
        "Modification des droits attachés aux catégories d'actions",
    )
