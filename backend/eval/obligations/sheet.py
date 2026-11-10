"""The sheet a lawyer reads to confirm an answer key.

A key is only a draft until a lawyer has read the WHOLE text and agreed with every atom and every not-duty (gold.py).
That reading is the slowest step of the whole baseline, so the sheet is made to be read, ticked and signed on paper or
in a browser, not edited as JSON: the full text with every labelled passage marked, then each atom and each not-duty as a
row to tick, then room for what the key missed, then a sign-off. It is one self-contained HTML file: no scripts, nothing
loaded from anywhere, and it prints cleanly (print to PDF to send it).

The sheet carries the key's fingerprint. A key that is edited afterwards has a new fingerprint, so a sheet that no longer
matches says so in plain words (`render_sheet` is deterministic: same key, same sheet).
"""

from __future__ import annotations

import html
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

from app.obligations.dates import date_state

from . import gold

REVIEW_DIR = gold.ROOT / "review"

_ANCHORS = {
    "effective_date": "the effective date", "expiration_date": "the expiry date", "signature_date": "signature",
    "termination": "termination", "notice_received": "receipt of the notice", "invoice_received": "receipt of the invoice",
    "request_received": "receipt of the request", "disclosure": "disclosure", "other": "the event the clause names",
}
_REASONS = {
    "right": "a right, not a duty", "effect": "an effect or consequence, not a task", "definition": "a definition",
    "representation": "a statement of fact (a representation)", "allocation": "an allocation of risk",
    "formality": "a formality",
}
_KINDS = {
    "duty": "Duty: must do", "restriction": "Restriction: must not do",
    "condition": "Condition: a step that gates a right", "commitment": "Commitment: a promise, not a task",
}


def timing_words(timing: dict) -> str:
    """The atom's timing in the plain words a lawyer would use (what the key says, not what the app computes). Every
    period the key carries is shown, whatever the atom's kind: the scorer grades a found atom's period and anchor whenever
    the key states them, so a lawyer must be able to see (and correct) what is there."""
    kind = timing.get("kind", "condition")
    lead = {"ongoing": "Ongoing", "condition": "A condition"}.get(kind)
    if kind == "deadline" and timing.get("explicit_date"):
        return f"On {timing['explicit_date']}"
    number, unit = timing.get("period_number"), (timing.get("period_unit") or "").replace("_", " ")
    anchor = _ANCHORS.get(timing.get("anchor") or "", "an event the clause names")
    if timing.get("anchor_text"):
        anchor += f" (“{timing['anchor_text']}”)"
    if number is None or not unit:
        if lead is None:
            return f"A deadline tied to {anchor}; the period is not stated"
        return "Ongoing: no single due date" if kind == "ongoing" else "A condition, or no fixed time"
    words = f"{number} {unit}{' (clear days)' if timing.get('clear_days') else ''} {timing.get('direction', 'after')} {anchor}"
    if not timing.get("stated_in_text"):
        words += ". Not stated in this contract: left for a person"
    return f"{lead}: {words}" if lead else words


def _who(key: gold.Gold, side: str) -> str:
    us = ", ".join(key.parties["us"]["names"])
    them = ", ".join(key.parties["them"]["names"])
    return {"us": us, "them": them, "both": f"both: {us} and {them}"}.get(side, side)


def _computed(key: gold.Gold, atom: gold.Atom) -> str:
    """What the app itself would make of this timing, in its own terms (app/obligations/dates.py): a date it counts, no
    fixed date by the duty's nature (an ongoing duty or a condition: not a gap), a date it waits for an event to give, or
    one a person has to set."""
    result = gold.atom_deadline(key, atom)
    if result.get("due"):
        return f"The app would count: due {result['due']}"
    if result.get("ends_on"):
        return f"The app would count: ends {result['ends_on']}"
    state = date_state(SimpleNamespace(due_date=None, metadata_json={"deadline": result}))
    return {
        "no_fixed_date": "The app would show no fixed date: none is expected",
        "waiting_event": "The app would wait for the event to be logged, then count",
    }.get(state, "The app would leave the date for a person")


def _marked_text(key: gold.Gold) -> str:
    """The whole text with every atom and not-duty passage marked and tagged (A01, N02...). Passages can overlap, so the
    text is cut at every boundary and each piece says which rows cover it."""
    spans = [(a.start, a.end, a.id, a.kind) for a in key.atoms] + [(n.start, n.end, n.id, "nd") for n in key.not_duties]
    cuts = sorted({0, len(key.text), *(s for s, _, _, _ in spans), *(e for _, e, _, _ in spans)})
    out = []
    for left, right in pairwise(cuts):
        piece = html.escape(key.text[left:right])
        covering = [(i, k) for s, e, i, k in spans if s <= left and e >= right]
        if not covering:
            out.append(piece)
            continue
        kind = next((k for _, k in covering if k != "nd"), "nd")
        ending = [i for s, e, i, _ in spans if e == right and s < right]
        tag = f"<sup>{html.escape(', '.join(ending))}</sup>" if ending else ""
        out.append(f'<mark class="{kind}" title="{html.escape(", ".join(i for i, _ in covering))}">{piece}</mark>{tag}')
    return "".join(out)


def _cell(text: str) -> str:
    return html.escape(text)


_STYLE = """
body{font:14px/1.5 Georgia,'Times New Roman',serif;color:#1a1a1a;max-width:1000px;margin:24px auto;padding:0 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid #999;padding-bottom:4px}
.meta{color:#444;font-size:12px}.box{border:1px solid #999;padding:10px 14px;background:#f7f7f2;margin:12px 0}
.draft{color:#9a3412;font-weight:bold}pre.text{white-space:pre-wrap;font:13px/1.55 Georgia,serif;border:1px solid #bbb;padding:12px}
mark{padding:0 1px;-webkit-print-color-adjust:exact;print-color-adjust:exact;border-bottom:2px solid #666}
mark.duty{background:#fde68a;border-color:#b45309}mark.restriction{background:#fca5a5;border-color:#b91c1c}
mark.condition{background:#bfdbfe;border-color:#1d4ed8}mark.commitment{background:#c7d2fe;border-color:#4338ca}
mark.nd{background:#e5e7eb;border-color:#6b7280;border-bottom-style:dotted}sup{font:bold 9px sans-serif;color:#444}
thead{display:table-header-group}
table{border-collapse:collapse;width:100%;margin:6px 0 14px}th,td{border:1px solid #888;padding:5px 7px;vertical-align:top;text-align:left}
th{background:#eee;font-size:12px}td.id{white-space:nowrap;font-weight:bold}td.decide{white-space:nowrap;font-size:13px}
tr{page-break-inside:avoid}.blank td{height:42px}.sign td{height:46px}
@media print{body{margin:0;max-width:none}.box{background:none}}
"""


def render_sheet(key: gold.Gold) -> str:
    """The review sheet for one key, as one HTML page."""
    status = key.status_problem
    state = ("<span class='draft'>DRAFT: no lawyer has confirmed it</span>" if status else "Lawyer-confirmed "
             f"by {_cell(key.status.get('confirmed_by', ''))} on {_cell(key.status.get('confirmed_at', ''))}")
    holes = gold.lint(key)
    rows_atoms = "".join(
        f"<tr><td class='id'>{a.id}</td><td>{_cell(a.clause)}</td><td>{_cell(a.quote)}"
        + (f"<br><em>Note: {_cell(a.note)}</em>" if a.note else "") + "</td>"
        f"<td>{_cell(_who(key, a.owed_by))}</td><td>{_cell(_KINDS[a.kind])}</td>"
        f"<td>{_cell(timing_words(a.timing))}<br><small>{_cell(_computed(key, a))}</small></td>"
        "<td class='decide'>☐ Correct<br>☐ Change<br>☐ Not a duty</td><td></td></tr>"
        for a in key.atoms)
    rows_not = "".join(
        f"<tr><td class='id'>{n.id}</td><td>{_cell(n.clause)}</td><td>{_cell(n.quote)}</td>"
        f"<td>{_cell(_REASONS[n.reason])}</td>"
        "<td class='decide'>☐ Correct<br>☐ It is a duty</td><td></td></tr>"
        for n in key.not_duties)
    missing = "".join("<tr class='blank'><td></td><td></td><td></td></tr>" for _ in range(8))
    hole_html = ("<ul>" + "".join(f"<li>{_cell(h)}</li>" for h in holes) + "</ul>") if holes else (
        "<p>None: every sentence with a duty word is inside a row above. This does not mean the text has no other duties "
        "(one can hide in a definition), so the reading of the whole text above still matters.</p>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Review sheet: {_cell(key.title)}</title><style>{_STYLE}</style></head>
<body>
<h1>Answer key review: {_cell(key.title)}</h1>
<p class="meta">Key <strong>{_cell(key.id)}</strong> &middot; fingerprint <strong>{key.fingerprint}</strong> &middot;
text sha256 {key.text_sha256[:16]}&hellip; &middot; {len(key.atoms)} atoms, {len(key.not_duties)} not-duties &middot; {state}</p>
<div class="box"><strong>What you are asked to do.</strong> Aegis reads contracts and lists what each side must do. To measure how well
it does that, we need a list of the right answers, and only a lawyer can say what they are. (1) Read the whole text below:
every passage we labelled is coloured and tagged. (2) For each row in the tables, tick one box. (3) Write anything we missed
at the end. (4) Sign. Your ticks decide whether this key is used to judge the software; nothing here changes any contract.
<br><br><strong>An atom is one actor doing one thing</strong> (&ldquo;pay the fees and give notice&rdquo; is two). Colours:
<mark class="duty">duty</mark> <mark class="restriction">restriction</mark> <mark class="condition">condition</mark>
<mark class="commitment">commitment</mark> <mark class="nd">not a duty</mark>. Text with no colour is something we say is
neither: read it for duties we missed.</div>
<h2>1. The whole text</h2>
<pre class="text">{_marked_text(key)}</pre>
<h2>2. Duties, restrictions, conditions and commitments</h2>
<table><thead><tr><th>Row</th><th>Clause</th><th>The words</th><th>Who owes it</th><th>Kind</th><th>When (and what the app would count)</th>
<th>Your decision</th><th>Comments</th></tr></thead><tbody>{rows_atoms}</tbody></table>
<h2>3. Passages we say are NOT duties</h2>
<table><thead><tr><th>Row</th><th>Clause</th><th>The words</th><th>Why not a duty</th><th>Your decision</th><th>Comments</th></tr></thead><tbody>{rows_not}</tbody></table>
<h2>4. Sentences with a duty word that no row above covers</h2>{hole_html}
<h2>5. Duties the key missed</h2>
<table><tr><th style="width:12%">Clause</th><th>The words in the text</th><th style="width:30%">Who owes it, and when</th></tr>{missing}</table>
<h2>6. Sign-off</h2>
<table class="sign"><tr><td>I have read the whole text and every row above, and the key is correct once my changes are made.</td></tr>
<tr><td>Name:</td></tr><tr><td>Signature:</td></tr><tr><td>Date:</td></tr></table>
<p class="meta">Made from key {_cell(key.id)}, fingerprint {key.fingerprint}. If the key is edited after this sheet was made, its
fingerprint changes and a confirmation of this sheet no longer covers it: ask for a new sheet.</p>
</body></html>
"""


def write_sheet(key: gold.Gold, folder: Path | None = None) -> Path:
    folder = folder or REVIEW_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{key.id}.html"
    path.write_text(render_sheet(key), encoding="utf-8")
    return path
