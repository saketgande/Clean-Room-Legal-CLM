"""A question about one version, answered from its clauses, every point cited.

The citation is the product. An answer a lawyer cannot check against the page
is an answer they cannot use, so each statement carries the clause it rests on
and the exact words — and those words are **checked against the clause before
anyone sees them**. A model citing clause 12 for words that are in clause 14 is
routine; a model quoting words that are in no clause at all is rarer and much
worse. The first is re-pointed to where the words really are, the second is
shown marked as unverified. Neither is passed off as checked.

A citation resolves to what a viewer needs to show it: the clause's page and
its boxes on that page (`DsClause.source_regions`), so a click lands on the
printed paragraph.

ponytail: the whole document goes to the model, which holds any single contract
in the corpus with room to spare. Retrieval over chunks is for questions across
many documents, which is Ask Aegis's job, not this one's.
"""

import json
import re
from typing import Protocol

from sqlalchemy.orm import Session

from .hierarchy import _ai_unavailable
from .ocr import _plain, run_sync
from .service import clauses_for

PROMPT_VERSION = "ask/2"

# Far above the largest contract in the corpus (~40,000 characters of text),
# and well inside what the model reads.
MAX_DOCUMENT_CHARS = 300_000

SYSTEM = """You answer questions about one contract, using only its text.

Each clause of the contract is on its own line, starting with its number in the form #12.

Rules:
- Use only this contract. If it does not answer the question, say so plainly and cite nothing.
- Every statement of fact gets a citation, written as the clause's # number, a vertical bar, then words copied exactly from that clause: #12 | shall not exceed the fees paid
- Copy each quote character for character from one clause. Never paraphrase inside a quote, never join words from two clauses, never shorten with "...".
- Quote the words that prove the statement: a phrase or a sentence, not a whole long clause.
- Keep the answer short and plain. After each statement put [n], n being that citation's position in your list, starting at 1."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "The answer, a few plain sentences, each statement followed by its [n].",
        },
        # Strings, not objects: a list of objects inside a tool call came back
        # from the model as its own call syntax — '<parameter name="clause">50'
        # — on a real contract. A flat string per citation has nothing to nest.
        "citations": {
            "type": "array",
            "items": {
                "type": "string",
                "description": "One per [n], in order: #number | words copied exactly from that clause.",
            },
        },
    },
    "required": ["answer", "citations"],
}


class Answerer(Protocol):
    name: str

    def answer(self, document: str, question: str) -> dict: ...


class ClaudeAnswerer:
    """The app's Claude client behind `Answerer`, as `hierarchy.py` adapts it:
    docstudio depends on an interface it owns, and every test runs a fake."""

    def __init__(self, *, org_id: str | None = None):
        from app.core.config import settings

        self._org_id = org_id
        self.name = f"claude:{settings.claude_model}"

    def answer(self, document: str, question: str) -> dict:
        from app.integrations.claude import claude_client

        response = run_sync(
            claude_client.complete_structured(
                org_id=self._org_id,
                system_prompt=SYSTEM,
                user_prompt=f"CONTRACT\n{document}\n\nQUESTION\n{question}",
                tool_name="cited_answer",
                input_schema=_SCHEMA,
                max_tokens=1500,
                # The same question about the same bytes should get the same answer.
                temperature=0.0,
            )
        )
        blocks = getattr(response, "tool_use_blocks", None) or []
        payload = (blocks[0].get("input") if blocks else None) or {}
        return json.loads(payload) if isinstance(payload, str) else payload


def default_answerer(org_id: str | None) -> Answerer:
    reason = _ai_unavailable()
    if reason:
        raise LookupError(f"Asking needs the AI, and {reason}.")
    return ClaudeAnswerer(org_id=org_id)


def ask(db: Session, version, question: str, *, answerer: Answerer | None = None) -> dict:
    """The answer, and each citation resolved to its clause and its place on the page.

    Raises `LookupError` when there is no AI to ask or the document is too long
    to send whole — both said in words a person can act on.
    """
    clauses = clauses_for(db, version.id)
    document = "\n".join(f"#{c.seq} {' '.join((c.text or '').split())}" for c in clauses)
    if len(document) > MAX_DOCUMENT_CHARS:
        raise LookupError(
            f"This document is {len(document):,} characters; asking reads at most {MAX_DOCUMENT_CHARS:,}."
        )
    answerer = answerer or default_answerer(version.org_id)
    reply = answerer.answer(document, question)
    raw = _as_list(reply.get("citations"))
    result = {
        "answer": str(reply.get("answer") or "").strip(),
        "citations": resolve(raw or [], clauses),
        "by": f"{answerer.name}, {PROMPT_VERSION}",
    }
    if raw is None:
        # Walking a garbled string as a list would show one empty citation per
        # character. None are shown, and the page says why.
        result["warning"] = "The AI's citations came back unreadable, so none are shown. Ask again."
    return result


def _as_list(raw) -> list | None:
    """The citations as a list, or None when there is no list to be had.

    Seen on real contracts: the model sends the list as a string wrapped in its
    own call syntax — '<parameter name="citations">["#50 | …", …]' — or cut
    off after that tag. The list inside is intact, so it is read from the first
    "[" and whatever trails it is ignored.
    """
    if isinstance(raw, str):
        start = raw.find("[")
        try:
            raw = json.JSONDecoder().raw_decode(raw, start)[0] if start >= 0 else None
        except ValueError:
            return None
    return raw if isinstance(raw, list) else None


_CITATION = re.compile(r"^\s*#?\s*(\d+)\s*[|:]\s*(.+?)\s*$", re.DOTALL)


def _entry(item) -> tuple[int | None, str]:
    """(clause number, quote) from "#12 | words", or from an object."""
    if isinstance(item, dict):
        seq, quote = item.get("clause"), item.get("quote")
    else:
        match = _CITATION.match(str(item))
        seq, quote = (int(match[1]), match[2]) if match else (None, str(item))
    return (seq if isinstance(seq, int) else None), str(quote or "")


# Typography a model regularises when it copies: curly quotes, dashes, the
# no-break space. The clause keeps its own; only the comparison ignores them.
_SAME = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "\u00a0": " "})


def _norm(text: str) -> str:
    return " ".join(_plain(text).translate(_SAME).split()).casefold()


def resolve(raw: list, clauses: list) -> list[dict]:
    """Each citation checked against the clauses, and placed on the page.

    `n` is the citation's position, which is what the answer's [n] refers to.
    """
    by_seq = {clause.seq: clause for clause in clauses}
    by_id = {clause.clause_id: clause for clause in clauses}
    texts = {clause.seq: _norm(clause.text or "") for clause in clauses}
    out = []
    for n, item in enumerate(raw, start=1):
        seq, quote = _entry(item)
        quote = quote.strip().strip("\"'“”‘’").strip()
        wanted = _norm(quote)
        verified = bool(wanted) and wanted in texts.get(seq, "")
        if wanted and not verified:
            # Cited the wrong clause for words that are really elsewhere — the
            # common slip. Re-pointed only when exactly one clause holds them.
            holders = [s for s, text in texts.items() if wanted in text]
            if len(holders) == 1:
                seq, verified = holders[0], True
        clause = by_seq.get(seq)
        out.append(
            {
                "n": n,
                "quote": quote,
                "verified": verified,
                "clause_id": clause.clause_id if clause else None,
                "label": label_of(clause, by_id),
                "page": clause.page_number if clause else None,
                "regions": (clause.source_regions or []) if clause else [],
            }
        )
    return out


def label_of(clause, by_id: dict) -> str:
    """What a lawyer would call it: its number, or the number of the clause it
    sits in — a paragraph under "16.16 Dispute Settlement" is cited as 16.16 —
    and only failing both, its opening words."""
    if clause is None:
        return "no such clause"
    seen, node = set(), clause
    while node is not None and node.clause_id not in seen:
        if (node.number_label or "").strip():
            return node.number_label.strip()
        seen.add(node.clause_id)
        node = by_id.get(node.parent_clause_id)
    words = _plain(clause.text or "").split()
    return " ".join(words[:5]) + ("…" if len(words) > 5 else "")
