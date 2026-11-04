"""The request in words and the extra instructions, as the requester filed them (spec 013).

The sentences are built in the browser; the server only keeps what was filed, after checking it.
`filed_summary` is read ONLY here (served by GET /intake/requests/{id}/summary behind `get_request`),
so nothing else - drafting, routing, triage - can ever act on an instruction (FR-31, FR-33).
"""

from __future__ import annotations

import logging
import unicodedata
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log, write_timeline_event
from app.core.database import utcnow
from app.intake.models import IntakeRequest

if TYPE_CHECKING:  # schemas imports this module's constants
    from app.intake.schemas import RequestSummaryFiling

logger = logging.getLogger(__name__)

WORD_LIMIT = 2000
CHAR_LIMIT = 20_000
MAX_INSTRUCTIONS = 100
TEXT_CUT = 20_000
ANSWER_CUT = 500
MAX_ACCEPTED = 50
MAX_DIFFERS = 10
LIMIT_MESSAGE = ("Your extra instructions are too long (over about 2,000 words). "
                 "Shorten them, or attach them as a file instead.")


def clean_text(s: str) -> str:
    """Plain text: no control characters except newline and tab (NUL cannot be stored), \\n line ends."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch) != "Cc").strip()


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def record_summary(db: Session, actor: User, request: IntakeRequest, summary: RequestSummaryFiling | None,
                   field_values: dict | None, http_request_id: str | None) -> None:
    """Keep the summary on the request; audit + timeline only when there is something to say.

    Runs on the request's own session inside the filing transaction (a second session would wait on
    the audit lock `intake.created` holds) and may never stop a filing (FR-9): any error rolls back
    only its savepoint.
    """
    from app.intake.agreement_forms import form_fields

    fv = field_values or {}
    fields = {f.key: f for f in form_fields(fv.get("request_form"))}
    if summary is None or not fields:
        return
    try:
        from app.intake.service import _coerce_field

        instructions = [
            {"text": i.text, "differs": [
                {"key": d.key, "label": fields[d.key].label, "answer": d.answer}
                for d in i.differs if d.key in fields]}
            for i in summary.instructions]
        accepted = []
        for a in summary.accepted:
            if a.key not in fields or a.key not in fv:
                continue
            try:
                if _coerce_field(fields[a.key], a.new) != fv[a.key]:
                    continue
            except Exception:
                continue
            accepted.append(a)
        if summary.text is None and not instructions:
            return
        n, m = len(instructions), len(accepted)
        with db.begin_nested():
            request.filed_summary = {"text": summary.text, "at": utcnow().isoformat(),
                                     "by_user_id": actor.id, "instructions": instructions}
            if not (n or m):
                return
            title = f"Filed with {_plural(n, 'extra instruction')} for Legal" if n else ""
            if n and m:
                title += f" · {_plural(m, 'answer')} changed from the summary"
            elif m:
                title = f"{_plural(m, 'answer')} changed from the summary before filing"
            changed = [{"key": a.key, "old": a.old, "new": a.new} for a in accepted]
            # No instruction or summary text in the audit row: it records that and how much, not what.
            write_audit_log(
                db, action="intake.summary_filed", resource_type="intake_request", resource_id=request.id,
                org_id=actor.org_id, actor_user_id=actor.id, request_id=http_request_id,
                after={"instructions": n, "answers_changed": changed})
            write_timeline_event(
                db, org_id=actor.org_id, resource_type="intake_request", resource_id=request.id,
                event_type="intake.summary_filed", title=title, actor_user_id=actor.id,
                request_id=http_request_id,
                details={"instructions": n, "answers_changed": [
                    {**c, "label": fields[c["key"]].label} for c in changed]})
    except Exception:
        logger.warning("could not record the summary for request %s", request.id, exc_info=True)
        request.filed_summary = None


def read(db: Session, request: IntakeRequest) -> dict:
    s = request.filed_summary if isinstance(request.filed_summary, dict) else None
    if not s:
        return {"text": None, "at": None, "by_name": None, "instructions": []}
    user = db.get(User, s.get("by_user_id")) if s.get("by_user_id") else None
    return {"text": s.get("text"), "at": s.get("at"),
            "by_name": (user.full_name or user.email) if user else None,
            "instructions": s.get("instructions") or []}
