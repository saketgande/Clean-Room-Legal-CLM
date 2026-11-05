"""What Ask Aegis is told about obligations, so it cannot say "there are none" when the truth is "they were never read".

Before this, `list_obligations` returned an empty list for a contract whose extraction failed or never ran, and the
assistant answered "no obligations". Now the result carries, beside the rows: each row's date state and why it has no
date, the portfolio's counts by date state, the Active contracts whose obligations are not read, and one plain
sentence (`summary`) that tells the model how to word what it found. The states are the ones the page shows
(app/obligations/dates.py and extraction_audit.py): one source, so the chat and the screen cannot disagree.
"""

from __future__ import annotations

from collections import defaultdict

from app.obligations.dates import date_counts, date_state
from app.obligations.extraction_audit import NOT_READ_STATES, needs_reading
from app.obligations.service import RETIRED_REASON

# How many not-read contracts a tool result lists; `unread_total` and the sentence carry the full count.
UNREAD_LISTED = 25

_STATE_WORDS = {
    "failed": "could not be read",
    "never": "not read yet",
    "no_text": "no readable text",
    "stale": "from an earlier version",
    "running": "being read now",
}


def date_note(ob) -> str | None:
    """Why an obligation has no date, in words a model will not misread as "not due". None when it has one."""
    state = date_state(ob)
    if state == "dated":
        return None
    block = (ob.metadata_json or {}).get("deadline") or {}
    if state == "not_read":
        return "Extracted before dates were counted: its timing is unknown. That is not the same as not due."
    if block.get("reason") == RETIRED_REASON:
        # The stored sentence is for the page's Edit button ("Step 3d", "re-run extraction"): a model repeats it, and a
        # re-run is refused for a contract already read.
        return "The date the AI first gave could not be counted from the contract, so it was removed. A person sets it."
    if state in ("needs_person", "waiting_event"):
        return block.get("reason") or "No date yet."
    return "Ongoing or conditional: it has no due date by its nature."


def _short_reason(state) -> str:
    """The sentence after "Obligations not read:" for the states in NOT_READ_STATES."""
    return {
        "failed": state.reason or "the last attempt did not finish.",
        "never": "they have not been read yet.",
        "no_text": "the contract has no readable text.",
        "stale": "they come from an earlier version of the document.",
    }[state.state]


def unread_contracts(db, user) -> list[dict]:
    """The Active contracts this person may open whose obligations are not read (or not yet), for the tool result."""
    return [
        {"contract_id": c.id, "title": c.title, "state": s.state, "reason": s.reason}
        for c, s in needs_reading(db, user)
    ]


def obligations_note(*, summary: dict, windowed: bool, unread: list[dict]) -> str:
    """The one sentence telling the model how to word the list: what has no date and why, and which contracts have no
    obligations on file because they were not read. Empty when there is nothing to qualify."""
    dates = summary["dates"]
    undated = sum(dates[s] for s in ("waiting_event", "needs_person", "no_fixed_date", "not_read"))
    parts = []
    if undated:
        parts.append(
            f"Open obligations across the portfolio: {summary['line']}. One with no date is NOT 'not due' and not "
            f"overdue: when you say what is due, say how many have no date and why ({summary['needs_date']} need a "
            "date set by a person)."
        )
        if windowed:
            parts.append(
                f"This list holds only obligations whose date falls inside the window, so the {undated} undated ones "
                "are not in it: do not say nothing else is due."
            )
    if unread:
        shown = ", ".join(f"{u['title']} ({_STATE_WORDS[u['state']]})" for u in unread[:5])
        more = f" and {len(unread) - 5} more" if len(unread) > 5 else ""
        parts.append(
            f"{len(unread)} Active contract{'s' if len(unread) != 1 else ''} may have no obligations on file because "
            f"they are not read: {shown}{more}. Never say these have none: say they are not read, or could not be, and "
            "that the contract page's Active stage shows why."
        )
    return " ".join(parts)


def date_gap_items(db, user) -> list[dict]:
    """My-attention items: one per contract with obligations that need a date set by a person."""
    from app.contracts.models import Contract
    from app.obligations.models import Obligation
    from app.obligations.service import visible_obligations

    rows = db.execute(
        visible_obligations(user).with_only_columns(
            Obligation.contract_id, Obligation.status, Obligation.due_date, Obligation.metadata_json, Contract.title
        )
    ).all()
    per_contract: dict[str, list] = defaultdict(list)
    for row in rows:
        per_contract[row.contract_id].append(row)
    items = []
    for contract_id, group in per_contract.items():
        needing = date_counts(group)["needs_date"]
        if needing:
            items.append({
                "contract_id": contract_id, "title": group[0].title,
                "reason": f"Obligations need a date: {needing} in {group[0].title}",
                "due_date": None, "urgency": "medium",
            })
    return items


def unread_items(db, user) -> list[dict]:
    """My-attention items: one per Active contract whose obligations are not read (failed, never, no text, or from an
    earlier version). One being read now is not an item: nothing is wrong yet."""
    return [
        {
            "contract_id": c.id, "title": c.title, "reason": f"Obligations not read: {_short_reason(s)}",
            "due_date": None, "urgency": "medium",
        }
        for c, s in needs_reading(db, user)
        if s.state in NOT_READ_STATES
    ]
