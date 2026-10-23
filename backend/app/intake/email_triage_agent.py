"""Email Intake Triage Agent.

Decides whether an inbound Gmail message is a Legal/CLM matter worth filing
(vs. newsletters, personal mail, order confirmations, etc.), and proposes the
Inbox "TYPE" column label — informed by attachment text once extracted, so a
terse "please review" email with an NDA attached still gets tagged NDA.

Classification is judgment, not string matching: a differently-worded or
informally-phrased email describing the same situation must classify the same
way, which a fixed keyword list can't guarantee. The real classifier is an LLM
call (_llm_classify, below); a deterministic keyword/filename heuristic is kept
ONLY as the fallback under settings.mock_claude and when the API call fails —
matching every other standalone intake agent in this codebase (see
litigation_agent.py, flow_agent.py): never raises, always returns a usable
decision.

The proposed type label is always one of agents.BUILTIN_EXTRA_TYPES — never a
raw string derived from the email itself, so the Inbox TYPE column always
matches one of the choices on the New Request form.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.intake import agents

logger = logging.getLogger(__name__)

# --- deterministic fallback (mock mode / LLM call failure only) ------------
#
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

_CATEGORIES = ("NDA", "Litigation", "Privacy", "Trademark", "Vendor",
               "Contract Review", "Policy/FAQ", "General")

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_clm_related": {
            "type": "boolean",
            "description": "True for a genuine legal/contract matter for an in-house legal team. False for "
                           "newsletters, marketing, personal correspondence, receipts, or anything not actually "
                           "a legal request.",
        },
        "category": {
            "type": ["string", "null"],
            "enum": [*_CATEGORIES, None],
            "description": "Best-fit category when is_clm_related is true; null when false.",
        },
        "confidence": {"type": "number", "description": "0.0-1.0 confidence in this classification."},
    },
    "required": ["is_clm_related", "category", "confidence"],
}


def _heuristic_is_clm_related(subject: str, body: str, attachment_filenames: list[str]) -> bool:
    """Deterministic keyword/filename check — used under mock mode or when
    the LLM call fails."""
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


def _prompt(subject: str, text: str) -> str:
    return f"EMAIL SUBJECT:\n{subject}\n\nEMAIL BODY / ATTACHMENT TEXT:\n{text[:6000]}"


def _llm_classify(db: Session, org_id: str, subject: str, text: str, *, claude_client=None) -> dict | None:
    """The real classifier: an LLM judges is_clm_related + category from the
    email's actual meaning, not a fixed keyword list. Returns None (never
    raises) if the call fails or returns unusable data — callers fall back to
    the deterministic heuristic above."""
    from app.ai.gateway import AICallContext, gateway_for

    user_prompt = _prompt(subject, text)
    try:
        # Feature "email_triage_agent" via the AI gateway: prompt + guard, token cap, output
        # check and ledger row in one place. Bad or cut-off output raises
        # into the except below, which keeps this agent's fallback.
        data = gateway_for(claude_client).structured_sync(
            db, "email_triage_agent", ctx=AICallContext(org_id=org_id),
            user_prompt=user_prompt, input_schema=_SCHEMA,
            log_input={"user_prompt": user_prompt},
        ).data
        category = data.get("category")
        if category not in _CATEGORIES:
            category = None
        conf = data.get("confidence")
        confidence = max(0.0, min(1.0, float(conf))) if isinstance(conf, (int, float)) else 0.5
        return {
            "is_clm_related": bool(data.get("is_clm_related")),
            "category": category,
            "confidence": confidence,
        }
    except Exception:
        logger.warning("email_triage_agent LLM classification failed; falling back to heuristic", exc_info=True)
        return None


def is_clm_related(db: Session, org_id: str, subject: str, body: str, attachment_filenames: list[str]) -> bool:
    """True if this looks like a legal/contract matter — judged by an LLM for
    semantic understanding, not keyword matching. Falls back to a
    deterministic keyword/filename heuristic under mock mode or if the API
    call fails."""
    from app.core.config import settings

    if settings.mock_claude:
        return _heuristic_is_clm_related(subject, body, attachment_filenames)

    text = body if not attachment_filenames else f"{body}\n\nAttached files: {', '.join(attachment_filenames)}"
    result = _llm_classify(db, org_id, subject, text)
    if result is None:
        return _heuristic_is_clm_related(subject, body, attachment_filenames)
    return result["is_clm_related"]


def classify_email(db: Session, org_id: str, subject: str, text: str) -> dict:
    """Runs the LLM classifier against subject + body (+ attachment excerpts
    once available) and proposes a `type_label` for the Inbox TYPE column.
    None when the classifier decides "General" — callers should leave the
    request's existing (already-canonical) type alone in that case rather than
    overwrite it with something generic."""
    from app.core.config import settings

    if not settings.mock_claude:
        llm = _llm_classify(db, org_id, subject, text)
        if llm is not None:
            category = llm["category"] if llm["is_clm_related"] else "General"
            result = agents.result_for_category(category, llm["confidence"], source="llm")
        else:
            result = agents.classify(subject, text)  # LLM call failed -> deterministic fallback
    else:
        result = agents.classify(subject, text)  # tests / mock mode -> deterministic, fast

    category = result.get("category")
    result["type_label"] = None
    if category == "General":
        return result
    result["type_label"] = agents.CATEGORY_TO_BUILTIN_EXTRA.get(category, agents.DEFAULT_BUILTIN_EXTRA)
    return result
