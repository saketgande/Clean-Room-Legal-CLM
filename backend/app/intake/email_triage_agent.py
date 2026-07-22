"""Email Intake Triage Agent.

Decides whether an inbound Gmail message is a Legal/CLM matter worth filing
(vs. newsletters, personal mail, order confirmations, etc.), and proposes the
Inbox "TYPE" column label from the same category rules the other intake
channels use (agents.classify) — informed by attachment text once extracted,
so a terse "please review" email with an NDA attached still gets tagged NDA.
"""

from __future__ import annotations

import re

from app.intake import agents

# Deliberately narrow and compound-phrase-heavy — a real inbox is full of
# newsletters, job alerts and invoices whose footers/boilerplate casually use
# generic words like "policy", "compliance" or "legal" (e.g. every marketing
# footer has a "Privacy Policy" link). Single generic words caused false
# positives in testing; only specific-enough legal/contract phrasing counts.
_CLM_KEYWORDS = (
    "nda", "non-disclosure", "non disclosure", "mutual nda",
    "msa", "master service agreement", "statement of work", "order form",
    "redline", "counterparty", "indemnif", "termination clause",
    "renewal notice", "contract renewal", "amendment to the agreement",
    "docusign", "e-signature request", "esignature request",
    "please sign the attached", "please review the attached agreement",
    "please review the contract", "contract review", "legal request",
    "litigation hold", "legal hold", "lawsuit", "subpoena",
    "data processing agreement", "dpia", "gdpr compliance",
    "privacy impact assessment", "data privacy assessment",
    "trademark application", "trademark registration",
    "vendor onboarding", "due diligence questionnaire",
)

# Short acronyms need word-boundary matching — plain substring search on
# "nda" false-positived inside "foundations", so every keyword is matched as
# a whole word/phrase, never a bare substring.
_CLM_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _CLM_KEYWORDS) + r")\b", re.IGNORECASE
)

_DOC_EXTENSIONS = (".pdf", ".doc", ".docx", ".rtf", ".odt")

# An attachment only counts as a CLM signal if its filename itself suggests a
# contract-shaped document — otherwise every invoice/receipt/report PDF in a
# real inbox would be misfiled as a legal request. Filenames are tokenized on
# non-alphanumeric characters so short acronyms (nda, msa, sow) only match a
# whole token, e.g. "NDA_Acme_2026.pdf", not a substring like "Agenda.pdf".
_CONTRACT_FILENAME_HINTS = (
    "agreement", "contract", "nda", "msa", "sow", "addendum", "amendment",
    "redline", "terms",
)
_SHORT_HINTS = {"nda", "msa", "sow"}

# category (from agents.classify) -> the Inbox "TYPE" column label.
_TYPE_LABELS = {
    "NDA": "NDA Request",
    "Litigation": "Litigation Hold",
    "Privacy": "Data Privacy Assessment",
    "Trademark": "Trademark Review",
    "Vendor": "Vendor Onboarding",
    "Contract Review": "Contract Review",
    "Policy/FAQ": "Policy / FAQ",
}


def is_clm_related(subject: str, body: str, attachment_filenames: list[str]) -> bool:
    """True if this looks like a legal/contract matter: a specific-enough
    keyword phrase in the subject/body, or an attachment whose filename
    itself suggests a contract/NDA (not just any document — a random PDF
    invoice or report doesn't count)."""
    text = f"{subject} {body}"
    if _CLM_PATTERN.search(text):
        return True
    for name in attachment_filenames:
        lower = name.lower()
        if not lower.endswith(_DOC_EXTENSIONS):
            continue
        tokens = set(re.split(r"[^a-z0-9]+", lower))
        for hint in _CONTRACT_FILENAME_HINTS:
            if hint in _SHORT_HINTS:
                if hint in tokens:
                    return True
            elif hint in lower:
                return True
    return False


def classify_email(subject: str, text: str) -> dict:
    """Runs the shared category classifier against subject + body (+
    attachment excerpts once available) and proposes a `type_label` for the
    Inbox TYPE column. `type_label` is None when the classifier falls back to
    "General" — callers should leave the original subject-derived label alone
    in that case rather than overwrite it with something generic."""
    result = agents.classify(subject, text)
    category = result.get("category")
    result["type_label"] = _TYPE_LABELS.get(category) if category != "General" else None
    return result
