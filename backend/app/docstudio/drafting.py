"""The AI marking up a contract: an instruction in, tracked changes out.

"Cap liability at 24 months of fees" becomes edits — exact old words of one
clause, the new words, and a reason for the margin — written into the Word file
by "AEGIS AI" as tracked changes the lawyer then accepts or rejects, the way
Mike's assistant proposes edits.

The model is not trusted to have copied the old words right. Each edit's words
must be in the clause it names — or in exactly one other, where it is moved,
as `ask.resolve` moves a citation — or it is dropped and said so. An edit on
words that are not in the contract would otherwise be a change nobody asked
for, placed wherever the words happened to match.
"""

import json
from typing import Protocol

from sqlalchemy.orm import Session

from .annotations import locate
from .ask import MAX_DOCUMENT_CHARS, _as_list, _norm
from .hierarchy import _ai_unavailable
from .ocr import run_sync
from .parsing.registry import DOCX_MIME
from .redline import Edit, suggest
from .service import clauses_for

PROMPT_VERSION = "drafting/1"
AUTHOR = "AEGIS AI"

SYSTEM = """You are a contracts lawyer marking up one contract in Word, as tracked changes.

Each clause of the contract is on its own line, starting with its number in the form #12.

Make the edits the instruction asks for, and only those:
- Each edit replaces exact words of one clause with new words. Copy the old words character for character from that one clause: a phrase or a sentence, not a whole clause unless all of it changes.
- To delete words, give empty new words. To add words, include a few existing words beside the addition in the old words, and repeat them in the new words.
- Keep the contract's style, defined terms and numbering. Change nothing the instruction does not need.
- Give each edit a one-sentence reason, as a lawyer would write it in the margin.
- If the contract needs no change for this instruction, return no edits and say why in the summary.

Return parallel lists: clauses[i], old[i], new[i] and why[i] are one edit."""

# Parallel lists of plain values, not a list of objects: objects nested in a
# tool call came back from the model as its own call syntax (see ask._SCHEMA).
_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "What was changed and why, in one or two sentences."},
        "clauses": {"type": "array", "items": {"type": "integer"}, "description": "The #number of each edit's clause."},
        "old": {"type": "array", "items": {"type": "string"}, "description": "Each edit's words, copied exactly."},
        "new": {"type": "array", "items": {"type": "string"}, "description": "Each edit's new words; empty to delete."},
        "why": {"type": "array", "items": {"type": "string"}, "description": "Each edit's reason, one sentence."},
    },
    "required": ["summary", "clauses", "old", "new", "why"],
}


class Drafter(Protocol):
    name: str

    def draft(self, document: str, instruction: str) -> dict: ...


class ClaudeDrafter:
    """The app's Claude client behind `Drafter`, adapted as `ask.ClaudeAnswerer` is."""

    def __init__(self, *, org_id: str | None = None):
        from app.core.config import settings

        self._org_id = org_id
        self.name = f"claude:{settings.claude_model}"

    def draft(self, document: str, instruction: str) -> dict:
        from app.integrations.claude import claude_client

        response = run_sync(
            claude_client.complete_structured(
                org_id=self._org_id,
                system_prompt=SYSTEM,
                user_prompt=f"CONTRACT\n{document}\n\nINSTRUCTION\n{instruction}",
                tool_name="tracked_changes",
                input_schema=_SCHEMA,
                max_tokens=4000,
                temperature=0.0,
            )
        )
        blocks = getattr(response, "tool_use_blocks", None) or []
        payload = (blocks[0].get("input") if blocks else None) or {}
        return json.loads(payload) if isinstance(payload, str) else payload


def default_drafter(org_id: str | None) -> Drafter:
    reason = _ai_unavailable()
    if reason:
        raise LookupError(f"Drafting needs the AI, and {reason}.")
    return ClaudeDrafter(org_id=org_id)


def checked_edits(reply: dict, clauses: list, flat_text: str) -> tuple[list[Edit], list[str]]:
    """The model's edits that are really in the contract, each with the text
    around it so the file finds the right place; and why each other was dropped."""
    by_seq = {clause.seq: clause for clause in clauses}
    texts = {clause.seq: _norm(clause.text or "") for clause in clauses}
    columns = [_as_list(reply.get(key)) or [] for key in ("clauses", "old", "new", "why")]
    edits, dropped = [], []
    for seq, old, new, why in zip(*columns, strict=False):
        old, new, why = str(old or "").strip(), str(new or ""), str(why or "").strip()
        wanted = _norm(old)
        if not wanted:
            dropped.append("An edit gave no words to change.")
            continue
        if wanted not in texts.get(seq, ""):
            holders = [s for s, text in texts.items() if wanted in text]
            if len(holders) != 1:
                dropped.append(f"“{old[:80]}” is not in the contract, so that edit was left out.")
                continue
            seq = holders[0]
        clause = by_seq[seq]
        try:
            lo, hi = locate(clause.text or "", old)
        except LookupError:
            lo = (clause.text or "").find(old)
            hi = lo + len(old)
        at = clause.char_start + max(lo, 0)
        end = clause.char_start + max(hi, 0)
        edits.append(Edit(find=old, replace=new, before=flat_text[max(0, at - 64) : at],
                          after=flat_text[end : end + 64], why=why))
    return edits, dropped


def draft(db: Session, version, instruction: str, *, drafter: Drafter | None = None) -> dict:
    """The instruction's edits by "AEGIS AI": tracked changes in a Word file's
    next version, or marks beside a PDF. Raises LookupError when there is no AI
    or nothing to change, and `redline.RedlineError` when no edit could be placed."""
    clauses = clauses_for(db, version.id)
    document = "\n".join(f"#{c.seq} {' '.join((c.text or '').split())}" for c in clauses)
    if len(document) > MAX_DOCUMENT_CHARS:
        raise LookupError(
            f"This document is {len(document):,} characters; drafting reads at most {MAX_DOCUMENT_CHARS:,}."
        )
    drafter = drafter or default_drafter(version.org_id)
    reply = drafter.draft(document, instruction)
    summary = str(reply.get("summary") or "").strip()
    edits, dropped = checked_edits(reply, clauses, version.flat_text or "")
    if not edits:
        raise LookupError(summary or (dropped[0] if dropped else "The AI proposed no edits."))
    if version.mime_type == DOCX_MIME:
        result, applied = suggest(db, version, edits, author=AUTHOR)
        version_id, placed, refused = result.version_id, [e for e, _ in applied.edits], applied.refused
    else:  # a PDF takes marks beside it, not changes in it (marks.py)
        from .marks import add

        done, refused = add(db, version, edits, author=AUTHOR, kind="ai")
        if not done:
            raise LookupError(refused[0][1] if refused else "The AI proposed no edits that could be placed.")
        version_id, placed = version.id, [Edit(m.anchor_quote_exact or "", m.proposed_text or "", why=m.body or "")
                                          for m in done]
    return {
        "version_id": version_id,
        "summary": summary,
        "placed": [{"find": e.find, "replace": e.replace, "why": e.why} for e in placed],
        "skipped": dropped + [f"“{e.find[:80]}”: {why}" for e, why in refused],
        "by": f"{drafter.name}, {PROMPT_VERSION}",
    }
