"""What Ask Aegis must know before it may file a request from the chat.

The chat tool used to file on the first message: the model picked a free-text type label
("Contract Review") and left out what Legal needs. The check runs BEFORE the confirmation
card, so a refusal reaches the model as a plain sentence and it asks the user instead.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from app.intake.agents import BUILTIN_EXTRA_TYPES
from app.intake.agreement_forms import form_def, form_defs


# A question to Legal needs no deal facts; every other type is a piece of work for a counterparty.
def _today() -> date:
    """The app's one "today": UTC (tests/test_one_today.py)."""
    return datetime.now(UTC).date()


QUESTION_TYPES = frozenset({"Legal Question — General", "IP Question"})

def _agreement_labels() -> tuple[str, ...]:
    """The labels the request wizard writes, so a chat request reads the same as a form one."""
    kinds = next(q["options"] for q in form_def("new_agreement")["fields"] if q["key"] == "agreement_type")
    kinds = [k if isinstance(k, str) else k.get("value") or k.get("label") for k in kinds]
    others = [f"{d['name']} Request" for d in form_defs() if d["key"] in ("sow", "dpa", "amendment", "renewal", "termination", "novation")]
    return tuple(f"New agreement · {k}" for k in kinds) + tuple(others)


# Agreements first: a vendor agreement is "New agreement · Buying from a vendor", never "Contract Review".
TYPE_LABELS = _agreement_labels() + BUILTIN_EXTRA_TYPES
_UNKNOWN = "Write 'not known' only if the user told you so."


def canonical_label(label: str) -> str | None:
    """The built-in type spelled as stored, or None when the label is not one of them."""
    return next((t for t in TYPE_LABELS if t.lower() == (label or "").strip().lower()), None)


def refusal(*, type_label: str, subject: str | None, description: str, department: str | None,
            counterparty: str | None, estimated_value: str | None, needed_by: str | None) -> str | None:
    """A sentence for the model saying what to ask the user first, or None when the request may be filed."""
    label = canonical_label(type_label)
    if label is None:
        return (f"'{type_label}' is not a request type. Use exactly one of: {', '.join(TYPE_LABELS)} "
                "('Other' when none fits). Do not file yet if you are unsure what the user needs: ask them.")
    missing = []
    if not (subject or "").strip():
        missing.append("a short subject")
    if len((description or "").strip()) < 30:
        missing.append("what exactly is needed, in the user's words")
    if not (department or "").strip():
        missing.append("which department it is for")
    if label not in QUESTION_TYPES:
        for value, name in ((counterparty, "the vendor or other party"),
                            (estimated_value, "the estimated value (with currency)"),
                            (needed_by, "the date it is needed by")):
            if not (value or "").strip():
                missing.append(name)
    try:
        past = date.fromisoformat((needed_by or "").strip()) < _today()
    except ValueError:
        past = False
    if past:
        return (f"Do not file yet. The needed-by date {needed_by.strip()} is in the past (today is {_today().isoformat()}). "
                "Use the year the user means, working from today's date, and call create_intake_request again.")
    if not missing:
        return None
    return ("Do not file yet. Ask the user for: " + "; ".join(missing) + ". "
            f"Never guess these. {_UNKNOWN} Then call create_intake_request again.")


def details_block(*, counterparty: str | None, estimated_value: str | None, needed_by: str | None) -> str:
    """The facts the user gave, appended to the description so triage and the lawyer read them."""
    lines = [f"{k}: {v.strip()}" for k, v in (("Counterparty", counterparty), ("Estimated value", estimated_value),
                                              ("Needed by", needed_by)) if (v or "").strip()]
    return "\n\n" + "\n".join(lines) if lines else ""


# generate_contract_docx writes free AI paper. Our template is what a request's draft step fills in, so the
# person picks first; without a template (a lease) free drafting is the only way and the model must say so.
DRAFT_CHOICE_REFUSAL = (
    "Do not draft yet. Ask the user which they want: (1) the organization's own template, which you draft "
    "directly with draft_from_template (no request is raised), or (2) free AI drafting here in the chat. "
    "Templates exist for NDA, MSA, consultancy, SOW, vendor, SaaS and DPA; for any other contract (a lease, "
    "say) tell the user there is no template and that a free draft is not our standard paper. "
    "Call generate_contract_docx again with user_chose_free_drafting=true only after they choose free drafting.")


def known_fields(*, counterparty: str | None, estimated_value: str | None) -> dict:
    """What the request page reads from ``field_values``: the counterparty's name and a numeric ``value`` (so
    "Deal value" is not "Not stated"). A value in words ("3 lakh") has no number to read and stays in the text."""
    out: dict = {}
    if (counterparty or "").strip().lower() not in ("", "not known"):
        out["counterparty"] = counterparty.strip()
    text = (estimated_value or "").strip()
    digits = text.replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", digits)
    if m and not re.search(r"[a-z]{4,}", re.sub(r"inr|usd|eur|gbp|rs", "", digits.lower())):
        out["value"] = m.group()
        cur = re.search(r"\b(INR|USD|EUR|GBP)\b", text.upper())
        if cur:
            out["currency"] = cur.group()
    return out


def register_lookup(db, actor, name: str | None) -> tuple[dict | None, list[str]]:
    """(the register's counterparty called exactly this, the other names that contain it). The wizard picks
    from the register; chat only has a typed name, so it is matched here instead of left as loose text."""
    from app.parties import service as parties

    if (name or "").strip().lower() in ("", "not known"):
        return None, []
    rows = parties.search(db, actor=actor, kind="counterparty", q=name.strip())
    exact = next((r for r in rows if r["name"].lower() == " ".join(name.split()).lower()), None)
    return exact, [r["name"] for r in rows if r is not exact]


def new_party_problem(db, actor, *, counterparty, jurisdiction, email) -> str | None:
    """A sentence for the model when the named counterparty is not in the register and cannot be added yet."""
    exact, similar = register_lookup(db, actor, counterparty)
    if exact or (counterparty or "").strip().lower() in ("", "not known"):
        return None
    if similar and not ((jurisdiction or "").strip() and (email or "").strip()):
        return (f"'{counterparty}' is not in the counterparty register, but these are: {', '.join(similar[:5])}. "
                "Ask the user whether one of them is the party; if so use that exact name.")
    missing = [w for v, w in ((jurisdiction, "its jurisdiction (country or state)"), (email, "a contact email")) if not (v or "").strip()]
    if missing:
        return (f"'{counterparty}' is a new counterparty and will be added to the register when the request is filed. "
                f"Ask the user for {' and '.join(missing)}, then call create_intake_request again.")
    return None

