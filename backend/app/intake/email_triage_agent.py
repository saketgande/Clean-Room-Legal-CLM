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

The proposed type is always either a real, active IntakeRequestType configured
for the org (so the request gets that type's dynamic fields/stage ladder, same
as filing it manually), or one of agents.BUILTIN_EXTRA_TYPES — never a raw
string derived from the email itself, so the Inbox TYPE column always matches
one of the choices on the New Request form.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.intake import agents
from app.intake.service import list_types

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

# category -> substrings to look for in a configured IntakeRequestType.name
# (case-insensitive) — e.g. category "NDA" matches a type named "NDA" or
# "Mutual NDA". No entry means always use the builtin extra fallback
# (agents.CATEGORY_TO_BUILTIN_EXTRA) for that category.
_CATEGORY_TYPE_NAME_HINTS = {
    "NDA": ("nda",),
    "Litigation": ("litigation",),
    "Privacy": ("privacy", "dpia", "data protection"),
    "Trademark": ("trademark",),
    "Vendor": ("vendor",),
    "Contract Review": ("contract review", "contract"),
    "Policy/FAQ": ("policy", "faq"),
}

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


def _llm_classify(db: Session, org_id: str, subject: str, text: str) -> dict | None:
    """The real classifier: an LLM judges is_clm_related + category from the
    email's actual meaning, not a fixed keyword list. Returns None (never
    raises) if the call fails or returns unusable data — callers fall back to
    the deterministic heuristic above."""
    from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD, get_agent_prompt, log_agent_call
    from app.ai.cost_guard import enforce_daily_token_cap
    from app.integrations.claude import ClaudeClient, run_coro_blocking

    bundle = get_agent_prompt(db, agent_id="email_triage_agent", org_id=org_id)
    user_prompt = _prompt(subject, text)
    try:
        enforce_daily_token_cap(org_id)
        resp = run_coro_blocking(lambda: ClaudeClient().complete_structured(
            system_prompt=bundle.skill_prompt + "\n\n" + UNTRUSTED_INPUT_GUARD,
            user_prompt=user_prompt,
            tool_name="classify_email", input_schema=_SCHEMA,
            max_tokens=200, temperature=0.0, model=bundle.model_name,
        ))
        log_agent_call(db, org_id=org_id, agent_id="email_triage_agent", prompt_bundle=bundle,
                        input_payload={"user_prompt": user_prompt}, response=resp)
        blocks = getattr(resp, "tool_use_blocks", None) or []
        data = blocks[0].get("input") if blocks else None
        if not isinstance(data, dict):
            return None
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
    once available) and proposes a `type_label` + `request_type_id` for the
    Inbox TYPE column. Both are None when the classifier decides "General" —
    callers should leave the request's existing (already-canonical) type
    alone in that case rather than overwrite it with something generic."""
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
    result["request_type_id"] = None
    if category == "General":
        return result

    matched = _match_configured_type(db, org_id, category)
    if matched:
        result["type_label"] = matched["name"] + " Request"
        result["request_type_id"] = matched["id"]
    else:
        result["type_label"] = agents.CATEGORY_TO_BUILTIN_EXTRA.get(category, agents.DEFAULT_BUILTIN_EXTRA)
    return result


def _match_configured_type(db: Session, org_id: str, category: str) -> dict | None:
    """Find an active, org-configured IntakeRequestType whose name plausibly
    corresponds to a classifier category, so an emailed NDA request gets the
    exact same request_type_id (and thus dynamic fields/stage ladder) a
    manually filed one would — instead of just a free-text label."""
    hints = _CATEGORY_TYPE_NAME_HINTS.get(category)
    if not hints:
        return None
    for t in list_types(db, org_id=org_id):
        name_lower = t["name"].lower()
        if any(hint in name_lower for hint in hints):
            return t
    return None
