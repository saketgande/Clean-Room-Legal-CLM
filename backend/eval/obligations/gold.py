"""The obligation answer key: load it, check it, lint it, work out its dates.

A key is one contract's text, frozen (texts/<id>.txt, with its sha256), plus a list of ATOMS: one actor, one
action, one trigger each (gold/<id>.json). Nothing here uses a database, the network or a model.

Atom kinds:
  duty / restriction   something a party must do / must not do. These decide the headline recall.
  condition            a step that gates a right ("30 days' notice before terminating"). Scored on its own line.
  commitment           a promise that is not a task ("grants a licence", "waives"). Scored on its own line.
A sentence a lawyer judged NOT to be a duty (a right, an effect, a definition, a representation, an allocation
of risk, a formality) goes in `not_duties`, so a row that quotes it counts against precision.

The due date of an atom is never typed: it is counted by production's own `obligation_deadline` from the atom's
timing and the key's anchors, so the key cannot drift from the code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
CONFIRMED = "lawyer_confirmed"
SCHEMA = 1
ATOM_KINDS = ("duty", "restriction", "condition", "commitment")
HEADLINE_KINDS = ("duty", "restriction")
NOT_DUTY_REASONS = ("right", "effect", "definition", "representation", "allocation", "formality")
SIDES = ("us", "them", "both")
ANCHOR_NAMES = ("effective_date", "expiration_date", "signature_date")

# A sentence with one of these must sit inside an atom or a not_duty, or the key has a hole.
DUTY_VERBS = re.compile(
    r"\b(shall|must|will|agrees?\s+to|required\s+to|undertakes?|payable|may\s+not|"
    r"(?:is|are)\s+responsible|warrants?)\b",
    re.IGNORECASE,
)


class GoldError(ValueError):
    """The key is not usable; the message lists every problem found."""


@dataclass(frozen=True)
class Atom:
    id: str
    clause: str
    quote: str
    owed_by: str
    kind: str
    type: str
    timing: dict
    start: int
    end: int
    note: str = ""


@dataclass(frozen=True)
class NotDuty:
    id: str
    clause: str
    quote: str
    reason: str
    start: int
    end: int


@dataclass(frozen=True)
class Gold:
    id: str
    title: str
    text: str
    text_sha256: str
    status: dict
    provenance: dict
    parties: dict
    anchors: dict
    atoms: list[Atom] = field(default_factory=list)
    not_duties: list[NotDuty] = field(default_factory=list)
    fingerprint: str = ""
    # What the production prompt shows about the contract (title, contract_type, counterparty_name). Not part of the
    # fingerprint: it changes what the model is shown, not what the right answer is.
    metadata: dict = field(default_factory=dict)

    @property
    def status_problem(self) -> str | None:
        """Why this key is not (or no longer) confirmed, in plain words; None when it is. A key counts as confirmed only
        when a person's name and a date are on it AND the fingerprint recorded at confirmation is still the key's own:
        a confirmation covers exactly the text, parties, anchors, atoms and not-duties the lawyer read, so an edit to any
        of them makes the key a draft again with no one having to remember to say so."""
        status = self.status
        if status.get("state") != CONFIRMED:
            return "no lawyer has confirmed it yet"
        if not status.get("confirmed_by") or not status.get("confirmed_at"):
            return "its confirmation does not say who confirmed it and when"
        if status.get("confirmed_fingerprint") != self.fingerprint:
            return "it was edited after it was confirmed (its text, parties, dates, atoms or not-duties changed)"
        return None

    @property
    def confirmed(self) -> bool:
        return self.status_problem is None

    @property
    def effective_status(self) -> str:
        """What every scorecard prints: "lawyer_confirmed" only for a key that is, else "draft"."""
        return CONFIRMED if self.confirmed else "draft"


def _span(text: str, quote: str) -> tuple[int, int] | None:
    first = text.find(quote)
    if first < 0 or text.find(quote, first + 1) >= 0:
        return None  # missing, or ambiguous: a quote must pick out one place
    return first, first + len(quote)


def _clause_base(clause: str) -> str:
    match = re.match(r"\d+(?:\.\d+)*", clause)
    return match.group() if match else clause


def _fingerprint(raw: dict, text_sha: str) -> str:
    body = {k: raw.get(k) for k in ("parties", "anchors", "atoms", "not_duties")}
    return hashlib.sha256(json.dumps([text_sha, body], sort_keys=True).encode()).hexdigest()[:16]


def problems(path: Path) -> list[str]:
    """Every reason this key file cannot be used (empty list = fine)."""
    from app.ai.schemas import ObligationTiming

    issues: list[str] = []
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("schema") != SCHEMA:
        issues.append(f"schema must be {SCHEMA}")
    # The id is written to the AI ledger's resource_id (36 characters) on every paid call; a longer one would lose
    # the call's row after the money was spent.
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,35}", str(raw.get("id", ""))):
        issues.append("id must be 1 to 36 lowercase letters, digits or hyphens")
    text_path = ROOT / raw.get("text_file", "")
    if not text_path.is_file():
        return issues + [f"text file {raw.get('text_file')!r} not found"]
    text = text_path.read_text(encoding="utf-8")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if sha != raw.get("text_sha256"):
        issues.append("text_sha256 does not match the text file: the text changed under the key")
    for side in ("us", "them"):
        if not (raw.get("parties", {}).get(side) or {}).get("names"):
            issues.append(f"parties.{side}.names is empty")
    for name, value in (raw.get("anchors") or {}).items():
        if name not in ANCHOR_NAMES:
            issues.append(f"unknown anchor {name!r}")
        elif value is not None:
            try:
                date.fromisoformat(value)
            except ValueError:
                issues.append(f"anchor {name} is not an ISO date")
    seen: set[str] = set()
    for entry in [*raw.get("atoms", []), *raw.get("not_duties", [])]:
        ident = entry.get("id", "?")
        if ident in seen:
            issues.append(f"duplicate id {ident}")
        seen.add(ident)
        if _span(text, entry.get("quote", "")) is None:
            issues.append(f"{ident}: quote is not in the text exactly once")
        base = _clause_base(entry.get("clause", ""))
        if not re.search(rf"(?m)^\s*{re.escape(base)}(?:\.|\s)", text):
            issues.append(f"{ident}: clause {entry.get('clause')!r} is not a numbered clause of the text")
    for atom in raw.get("atoms", []):
        if atom.get("kind") not in ATOM_KINDS:
            issues.append(f"{atom.get('id')}: kind must be one of {ATOM_KINDS}")
        if atom.get("owed_by") not in SIDES:
            issues.append(f"{atom.get('id')}: owed_by must be one of {SIDES}")
        try:
            ObligationTiming(**atom.get("timing", {}))
        except Exception as exc:  # any malformed timing is a key problem to report, not a crash
            issues.append(f"{atom.get('id')}: timing is not valid: {exc}")
    for item in raw.get("not_duties", []):
        if item.get("reason") not in NOT_DUTY_REASONS:
            issues.append(f"{item.get('id')}: reason must be one of {NOT_DUTY_REASONS}")
    return issues


def load(path: Path) -> Gold:
    found = problems(path)
    if found:
        raise GoldError(f"{Path(path).name}: " + "; ".join(found))
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    text = (ROOT / raw["text_file"]).read_text(encoding="utf-8")
    atoms = [
        Atom(a["id"], a["clause"], a["quote"], a["owed_by"], a["kind"], a.get("type", ""),
             a.get("timing", {}), *_span(text, a["quote"]), a.get("note", ""))
        for a in raw["atoms"]
    ]
    not_duties = [
        NotDuty(n["id"], n["clause"], n["quote"], n["reason"], *_span(text, n["quote"]))
        for n in raw.get("not_duties", [])
    ]
    return Gold(
        id=raw["id"], title=raw.get("title", raw["id"]), text=text, text_sha256=raw["text_sha256"],
        status=raw.get("status", {}), provenance=raw.get("provenance", {}), parties=raw["parties"],
        anchors=raw.get("anchors", {}), atoms=atoms, not_duties=not_duties,
        fingerprint=_fingerprint(raw, raw["text_sha256"]), metadata=raw.get("metadata", {}),
    )


def key_files() -> list[Path]:
    return sorted((ROOT / "gold").glob("*.json"))


def load_all() -> list[Gold]:
    return [load(path) for path in key_files()]


def lint(gold: Gold) -> list[str]:
    """Sentences with a duty verb that no atom or not_duty covers: the holes a lawyer must close.
    A safety net only; it cannot see a duty hidden in a definition, so the whole text still needs reading."""
    covered = [(a.start, a.end) for a in gold.atoms] + [(n.start, n.end) for n in gold.not_duties]
    holes = []
    for match in re.finditer(r"[^.;\n]+[.;]?", gold.text):
        sentence = match.group()
        if not DUTY_VERBS.search(sentence):
            continue
        if not any(min(end, match.end()) - max(start, match.start()) > 0 for start, end in covered):
            holes.append(sentence.strip()[:100])
    return holes


def anchors_for(gold: Gold) -> dict:
    """The key's contract dates as production's Anchor objects."""
    from app.core.deadlines import Anchor

    return {
        name: Anchor(name, date.fromisoformat(gold.anchors[name]) if gold.anchors.get(name) else None, "user")
        for name in ANCHOR_NAMES
    }


def atom_deadline(gold: Gold, atom: Atom) -> dict:
    """What production's own code makes of this atom's timing: the due date (None when it needs a person or an
    event nobody logged), the end date of an ongoing duty, and the reason there is none."""
    from app.ai.schemas import ObligationTiming
    from app.obligations.service import obligation_deadline

    return obligation_deadline(ObligationTiming(**atom.timing), anchors_for(gold), quote=atom.quote)


def write_confirmation(path: Path, *, by: str, at: str, fingerprint: str) -> None:
    """Record a lawyer's confirmation in the key file: who, when, and the fingerprint of exactly what they confirmed.
    Only the `status` block is rewritten, so the rest of a hand-formatted key file keeps its layout (and its diff). The
    caller has already checked the key and asked a person."""
    path = Path(path)
    raw = path.read_bytes().decode("utf-8")  # bytes, so a CRLF file stays CRLF (read_text would flatten it)
    decoder = json.JSONDecoder()
    marker = raw.index('"status"')
    start = raw.index("{", marker)
    old, end = decoder.raw_decode(raw, start)
    new = {**old, "state": CONFIRMED, "confirmed_by": by, "confirmed_at": at, "confirmed_fingerprint": fingerprint}
    block = json.dumps(new, indent=2, ensure_ascii=False).replace("\n", "\n  ")
    if "\r\n" in raw:
        block = block.replace("\n", "\r\n")
    updated = raw[:start] + block + raw[end:]
    before, after = json.loads(raw), json.loads(updated)
    if {k: v for k, v in before.items() if k != "status"} != {k: v for k, v in after.items() if k != "status"}:
        raise GoldError("refusing to write: the confirmation would have changed more than the status block")
    # Encode first (a name that cannot be encoded raises here, with the key untouched), then replace the file in one
    # step: write_text truncates the only copy of a hand-built key before it writes the new one.
    data = updated.encode("utf-8")
    scratch = path.with_name(path.name + ".tmp")
    scratch.write_bytes(data)
    os.replace(scratch, path)
