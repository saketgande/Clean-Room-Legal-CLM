"""Notice extraction agent.

Reads the text of an uploaded legal notice and proposes the register fields —
who sent it, what kind of notice it is, the date on it, and (the one that
matters) the response deadline.

Two deliberate choices:

* **It suggests, it never commits.** Every value comes back as a suggestion the
  filer reviews in the form before saving. A statutory deadline inferred wrongly
  and applied silently is precisely the failure this module exists to prevent,
  so a human always confirms the date.
* **It never raises.** Like every other standalone agent here (litigation_agent,
  flow_agent, email_triage_agent), a failed or mocked LLM call degrades to a
  deterministic regex pass rather than blocking the upload — filing the notice
  must always be possible, extraction is a convenience on top.

The document text is attacker-supplied by definition: it arrives from an
opposing party. UNTRUSTED_INPUT_GUARD is appended to the system prompt so the
model treats it as data, and every returned value is validated against the
register's own vocabularies before it reaches the caller.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.notices.models import NOTICE_TYPES

logger = logging.getLogger(__name__)

# Cap what we send: notices are short, and the deadline/counterparty always sit
# in the opening page or the prayer clause near the end. 12k chars comfortably
# covers both without paying for a 60-page exhibit bundle.
_MAX_CHARS = 12000

_SCHEMA = {
    "type": "object",
    "properties": {
        "counterparty_name": {
            "type": ["string", "null"],
            "description": "The party that SENT the notice (or, for an outbound notice, its recipient). Legal entity name as written.",
        },
        "counterparty_ref": {
            "type": ["string", "null"],
            "description": "The sender's own reference/file number for this notice, if printed on it.",
        },
        "notice_type": {
            "type": ["string", "null"],
            "enum": [*NOTICE_TYPES, None],
            "description": "Best-fit category for this notice.",
        },
        "subject": {
            "type": ["string", "null"],
            "description": "A short factual title, under 120 characters.",
        },
        "notice_date": {
            "type": ["string", "null"],
            "description": "Date printed on the notice, as YYYY-MM-DD.",
        },
        "response_due_date": {
            "type": ["string", "null"],
            "description": "Date by which a response is demanded, as YYYY-MM-DD. Only if the notice states a deadline or a period ('within 14 days') that can be resolved against the notice date.",
        },
        "demanded_action": {
            "type": ["string", "null"],
            "description": "One or two sentences: what the notice demands, and by when.",
        },
        "confidence": {
            "type": "number",
            "description": "0.0-1.0 — how confident you are overall. Be honest; low for a scanned or partial document.",
        },
    },
    "required": ["confidence"],
}


def _prompt(text: str) -> str:
    return (
        "Extract the register fields from this legal notice.\n\n"
        "Rules:\n"
        "- Use only what the document states. Never invent a deadline.\n"
        "- If the notice gives a period rather than a date (e.g. 'within 15 days "
        "hereof'), resolve it against the notice's own date and return the "
        "resulting date. If the notice date is itself unclear, return null.\n"
        "- counterparty_name is the OTHER side — the sender of a notice served on "
        "us, not our own company.\n\n"
        f"--- NOTICE TEXT ---\n{text[:_MAX_CHARS]}\n--- END ---"
    )


# --- deterministic fallback (mock mode / LLM failure only) -----------------
#
# Deliberately conservative: it only reports what it can see literally. It will
# not resolve "within 30 days" into a date — guessing a statutory deadline from
# a regex is exactly the kind of confident-but-wrong behaviour that would make
# the register untrustworthy.
_DATE_PATTERNS = (
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),                       # 2026-07-28
    re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b"),             # 28/07/2026
)
_TYPE_HINTS = (
    ("cease_and_desist", ("cease and desist", "cease-and-desist")),
    ("demand", ("demand notice", "letter of demand", "demand for payment")),
    ("termination", ("notice of termination", "terminate the agreement")),
    ("breach", ("breach of contract", "material breach", "notice of breach")),
    ("infringement", ("infringement", "passing off", "trademark violation")),
    ("recovery", ("recovery of dues", "outstanding dues", "recovery proceedings")),
    ("statutory", ("under section", "statutory notice", "u/s ")),
)


def _heuristic(text: str) -> dict:
    low = text.lower()
    notice_type = None
    for candidate, hints in _TYPE_HINTS:
        if any(h in low for h in hints):
            notice_type = candidate
            break
    notice_date = None
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            if pattern is _DATE_PATTERNS[0]:
                notice_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            else:
                notice_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            notice_date = None
        if notice_date:
            break
    return {
        "counterparty_name": None,
        "counterparty_ref": None,
        "notice_type": notice_type,
        "subject": None,
        "notice_date": notice_date,
        # Never guessed by the heuristic — see the note above.
        "response_due_date": None,
        "demanded_action": None,
        "confidence": 0.2 if (notice_type or notice_date) else 0.0,
        "source": "heuristic",
    }


def _clean_date(value) -> str | None:
    """Accept only a real ISO date. The model is asked for YYYY-MM-DD, but a
    malformed value must not reach a date column — or worse, a deadline field."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _clean_str(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] or None


def extract_notice_fields(db: Session, *, org_id: str, text: str) -> dict:
    """Propose register fields from a notice's text. Always returns a dict;
    `source` is 'llm' or 'heuristic' so the UI can say where a value came from."""
    from app.core.config import settings

    text = (text or "").strip()
    if not text:
        return {**_heuristic(""), "source": "empty"}
    if settings.mock_claude:
        return _heuristic(text)

    from app.ai.agent_catalog import UNTRUSTED_INPUT_GUARD, get_agent_prompt, log_agent_call
    from app.ai.cost_guard import enforce_daily_token_cap
    from app.integrations.claude import ClaudeClient, run_coro_blocking

    bundle = get_agent_prompt(db, agent_id="notice_extraction_agent", org_id=org_id)
    user_prompt = _prompt(text)
    try:
        enforce_daily_token_cap(org_id)
        resp = run_coro_blocking(lambda: ClaudeClient().complete_structured(
            system_prompt=bundle.skill_prompt + "\n\n" + UNTRUSTED_INPUT_GUARD,
            user_prompt=user_prompt,
            tool_name="extract_notice_fields", input_schema=_SCHEMA,
            max_tokens=700, temperature=0.0, model=bundle.model_name,
        ))
        log_agent_call(
            db, org_id=org_id, agent_id="notice_extraction_agent", prompt_bundle=bundle,
            input_payload={"chars": len(text)}, response=resp,
        )
        blocks = getattr(resp, "tool_use_blocks", None) or []
        data = blocks[0].get("input") if blocks else None
        if not isinstance(data, dict):
            return _heuristic(text)

        notice_type = data.get("notice_type")
        if notice_type not in NOTICE_TYPES:
            notice_type = None
        conf = data.get("confidence")
        confidence = max(0.0, min(1.0, float(conf))) if isinstance(conf, (int, float)) else 0.5
        return {
            "counterparty_name": _clean_str(data.get("counterparty_name"), 200),
            "counterparty_ref": _clean_str(data.get("counterparty_ref"), 120),
            "notice_type": notice_type,
            "subject": _clean_str(data.get("subject"), 200),
            "notice_date": _clean_date(data.get("notice_date")),
            "response_due_date": _clean_date(data.get("response_due_date")),
            "demanded_action": _clean_str(data.get("demanded_action"), 2000),
            "confidence": confidence,
            "source": "llm",
        }
    except Exception:
        logger.warning(
            "notice_extraction_agent failed; falling back to heuristic", exc_info=True
        )
        return _heuristic(text)
