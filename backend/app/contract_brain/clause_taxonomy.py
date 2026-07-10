"""Canonical clause taxonomy (CUAD-aligned).

The LLM extractor returns free-text clause labels — "Confidentiality
Obligations", "confidentiality", "Confidential Information Definition" all
describe the same category. Left un-normalized, filters, search facets and
playbook matching fragment across variants. This collapses raw labels to a
stable canonical key so the same clause type always compares equal.

Canonical keys are a pragmatic subset of the CUAD (Contract Understanding
Atticus Dataset) category set, plus the common commercial-contract types the
extractor produces. Unknown labels fall back to a normalized slug so casing
and spacing variants still collapse.
"""

import re

# raw-slug -> canonical key. Keys on the LEFT are the normalized form
# (lowercase, non-alphanumeric -> "_"), so "Confidentiality Obligations" and
# "confidentiality_obligations" both match the same entry.
_ALIASES: dict[str, str] = {
    # Confidentiality family
    "confidentiality": "confidentiality",
    "confidentiality_obligations": "confidentiality",
    "confidential_information": "confidentiality",
    "confidential_information_definition": "confidentiality",
    "confidentiality_exclusions": "confidentiality",
    "nondisclosure": "confidentiality",
    "non_disclosure": "confidentiality",
    # Term / termination
    "term": "term_and_termination",
    "termination": "term_and_termination",
    "term_and_termination": "term_and_termination",
    "term_termination": "term_and_termination",
    # Governing law / jurisdiction
    "governing_law": "governing_law",
    "governing_law_jurisdiction": "governing_law",
    "choice_of_law": "governing_law",
    "jurisdiction": "governing_law",
    # Liability
    "limitation_of_liability": "limitation_of_liability",
    "liability": "limitation_of_liability",
    "cap_on_liability": "limitation_of_liability",
    # Indemnity
    "indemnification": "indemnification",
    "indemnity": "indemnification",
    # Data protection / privacy
    "data_protection": "data_protection",
    "data_privacy": "data_protection",
    "privacy": "data_protection",
    "gdpr": "data_protection",
    # IP
    "ip_ownership": "ip_ownership",
    "intellectual_property": "ip_ownership",
    "ip": "ip_ownership",
    "ownership": "ip_ownership",
    "license_grant": "license_grant",
    # Assignment / change of control
    "assignment": "assignment",
    "anti_assignment": "assignment",
    "change_of_control": "assignment",
    # Remedies / dispute
    "remedies": "remedies",
    "equitable_remedies": "remedies",
    "dispute_resolution": "dispute_resolution",
    "arbitration": "dispute_resolution",
    # Payment / fees
    "payment": "payment_terms",
    "payment_terms": "payment_terms",
    "fees": "payment_terms",
    "pricing": "payment_terms",
    # Warranties / reps
    "representations_and_warranties": "representations_and_warranties",
    "warranties": "representations_and_warranties",
    "representations": "representations_and_warranties",
    "warranty": "representations_and_warranties",
    # Boilerplate
    "entire_agreement": "entire_agreement",
    "amendment": "amendment",
    "notices": "notices",
    "notice": "notices",
    "force_majeure": "force_majeure",
    "severability": "severability",
    "waiver": "waiver",
    "counterparts": "counterparts",
    # Restrictive covenants
    "non_solicitation": "non_solicitation",
    "nonsolicitation": "non_solicitation",
    "non_solicit": "non_solicitation",
    "non_compete": "non_compete",
    "noncompete": "non_compete",
    "exclusivity": "exclusivity",
    # Renewal / SLA / audit
    "renewal": "renewal",
    "auto_renewal": "renewal",
    "sla": "service_levels",
    "service_levels": "service_levels",
    "audit": "audit_rights",
    "insurance": "insurance",
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").lower()).strip("_")


def canonical_clause_type(raw: str | None) -> str:
    """Map any raw clause label to its canonical key. Unknown labels collapse
    to a normalized slug so at least casing/spacing variants unify."""
    if not raw:
        return "unknown"
    slug = _slug(raw)
    return _ALIASES.get(slug, slug)


def display_label(canonical: str) -> str:
    """Human-friendly label for a canonical key ('term_and_termination' ->
    'Term And Termination')."""
    return (canonical or "").replace("_", " ").title()


# Business-impact weight (1-10) per canonical clause type — how much a bad
# version of this clause hurts. Drives the weighted risk score: high-weight
# clauses (liability, IP, data) move the number far more than boilerplate.
# Aligned with the LexCheck/Sirion weighting conventions.
CLAUSE_WEIGHTS: dict[str, int] = {
    "limitation_of_liability": 10,
    "indemnification": 9,
    "data_protection": 9,
    "ip_ownership": 9,
    "confidentiality": 7,
    "license_grant": 7,
    "non_compete": 7,
    "payment_terms": 6,
    "term_and_termination": 6,
    "non_solicitation": 6,
    "dispute_resolution": 6,
    "warranty": 6,
    "representations_and_warranties": 6,
    "exclusivity": 6,
    "assignment": 5,
    "renewal": 5,
    "audit_rights": 5,
    "service_levels": 5,
    "insurance": 5,
    "remedies": 5,
    "governing_law": 3,
    "notices": 2,
    "force_majeure": 2,
    "entire_agreement": 2,
    "severability": 2,
    "amendment": 2,
    "waiver": 2,
    "counterparts": 1,
}

DEFAULT_CLAUSE_WEIGHT = 4


def clause_weight(canonical: str | None) -> int:
    """Business-impact weight (1-10) for a canonical clause type."""
    return CLAUSE_WEIGHTS.get(canonical or "", DEFAULT_CLAUSE_WEIGHT)
