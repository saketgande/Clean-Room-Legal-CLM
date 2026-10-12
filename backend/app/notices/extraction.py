"""Notice extraction agent.

Reads the text of an uploaded legal notice and proposes the register fields —
who sent it, what kind of notice it is, the date on it, and (the one that
matters) the WORDING of the response deadline. The model never returns a date
it worked out: it copies the period and what it runs from, and code counts the
date (service.compute_due) so a person sees the formula, not a guess.

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

from app.core.deadlines import date_in_quote
from app.notices.models import NOTICE_TYPES

# The deadline vocabularies the model may answer with. Months and longer are rare
# in a notice; "years" is left out on purpose, matching the system prompt.
PERIOD_UNITS = ("days", "business_days", "weeks", "months")
RUNS_FROM = ("notice_date", "receipt", "other")

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
        "deadline": {
            "type": "object",
            "description": "The notice's own wording of the response deadline. Copy it; never compute a date.",
            "properties": {
                "stated": {
                    "type": "boolean",
                    "description": "True only when the notice itself writes a deadline (a date or a period). False when none is written or one would come only from a statute or custom.",
                },
                "explicit_date": {
                    "type": ["string", "null"],
                    "description": "A calendar date the notice writes out as the deadline, as YYYY-MM-DD. Null when it gives a period.",
                },
                "period_count": {"type": ["integer", "null"], "minimum": 0},
                "clear_days": {
                    "type": "boolean",
                    "description": "True only when the notice says the days are clear days.",
                },
                "period_unit": {"type": ["string", "null"], "enum": [*PERIOD_UNITS, None]},
                "runs_from": {"type": ["string", "null"], "enum": [*RUNS_FROM, None]},
                "runs_from_text": {
                    "type": ["string", "null"],
                    "description": "What the period runs from, in the notice's words.",
                },
                "quote": {
                    "type": ["string", "null"],
                    "description": "The sentence that states the deadline.",
                },
            },
            "required": ["stated"],
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
        "- For the deadline, copy the wording: the period as written, whether it "
        "says clear days, and what it runs from. Never work out a date from a "
        "period; code does that.\n"
        "- counterparty_name is the OTHER side — the sender of a notice served on "
        "us, not our own company.\n\n"
        f"--- NOTICE TEXT ---\n{text[:_MAX_CHARS]}\n--- END ---"
    )


# --- deterministic fallback (mock mode / LLM failure only) -----------------
#
# Deliberately conservative: it only reports what it can see literally. It will
# not even read "within 30 days" as deadline wording — reading a statutory
# deadline from a regex is exactly the kind of confident-but-wrong behaviour that
# would make the register untrustworthy. No deadline reading at all (None) is
# different from "the notice states none" ({"stated": False}).
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
        "deadline": None,
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
        # Naive is fine: only the calendar date survives .date().
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date().isoformat()  # noqa: DTZ007
    except ValueError:
        return None


def _clean_str(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] or None


def clean_deadline(value, *, text: str | None = None) -> dict | None:
    """The model's deadline wording, checked against the vocabularies, or None
    when there is no reading. Only these facts are kept; they are what code
    counts from, so a malformed value must not reach compute_due.

    An explicit date survives only when its day and year are written in the
    quote (not merely somewhere in the notice: almost any notice has the year and
    some matching day number); otherwise the period (if any) is counted, or a
    person sets the date. The period's own number is checked against the quote
    by compute_due, which also sees facts a client sends back on create."""
    if not isinstance(value, dict):
        return None
    if value.get("stated") is not True:
        # Nothing else survives "not stated": a stray period here would read as
        # a deadline the notice never gave.
        return {"stated": False, "explicit_date": None, "period_count": None,
                "period_unit": None, "clear_days": False, "runs_from": None,
                "runs_from_text": None, "quote": None}
    count = value.get("period_count")
    count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
    unit = value.get("period_unit") if value.get("period_unit") in PERIOD_UNITS else None
    runs_from = value.get("runs_from") if value.get("runs_from") in RUNS_FROM else None
    quote = _clean_str(value.get("quote"), 1000)
    explicit = _clean_date(value.get("explicit_date"))
    # A date the model worked out itself (a period it counted, a year it assumed)
    # is not written in the notice, so it is no deadline.
    if explicit and not date_in_quote(date.fromisoformat(explicit), quote):
        explicit = None
    return {
        "stated": True,
        "explicit_date": explicit,
        "period_count": count,
        "period_unit": unit,
        "clear_days": value.get("clear_days") is True,
        "runs_from": runs_from,
        "runs_from_text": _clean_str(value.get("runs_from_text"), 300),
        "quote": quote,
    }


def extract_notice_fields(db: Session, *, org_id: str, text: str, claude_client=None) -> dict:
    """Propose register fields from a notice's text. Always returns a dict;
    `source` is 'llm' or 'heuristic' so the UI can say where a value came from."""
    from app.core.config import settings

    text = (text or "").strip()
    if not text:
        return {**_heuristic(""), "source": "empty"}
    if settings.mock_claude:
        return _heuristic(text)

    from app.ai.gateway import AICallContext, gateway_for

    user_prompt = _prompt(text)
    try:
        # Feature "notice_extraction_agent" via the AI gateway: prompt + guard, token cap, output
        # check and ledger row in one place. Bad or cut-off output raises
        # into the except below, which keeps this agent's fallback.
        data = gateway_for(claude_client).structured_sync(
            db, "notice_extraction_agent", ctx=AICallContext(org_id=org_id),
            user_prompt=user_prompt, input_schema=_SCHEMA,
            log_input={"chars": len(text)},
        ).data

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
            "deadline": clean_deadline(data.get("deadline"), text=text),
            "demanded_action": _clean_str(data.get("demanded_action"), 2000),
            "confidence": confidence,
            "source": "llm",
        }
    except Exception:
        logger.warning(
            "notice_extraction_agent failed; falling back to heuristic", exc_info=True
        )
        return _heuristic(text)
