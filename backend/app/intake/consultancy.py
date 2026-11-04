"""What the Consultancy template's Engagement Schedule is filled with from a request's answers (spec 008).

Pure functions, no database and no audit, shaped like ``intake.sow``: the four Consultancy fill-in
fields (``consultancy_values``), the example reading the Templates page check and preview use, the
facts a draft records and the hold message. It imports only the stdlib, settings and ``sow`` so that
both ``intake.drafting`` and ``drafting_templates.service`` can import it without a cycle.

Money is exact ``Decimal`` (``sow.total_value`` / ``sow.money``). A real request is filled only from
its own answers and an answer it lacks fills with nothing: example content (``EXAMPLE_VALUES``) is
returned only when a caller passes ``example=True``, which only the Templates page check and preview
do (FR-34). The standard sentences (the fee cap, the consent to replace a key individual, the
Company contact's role) are the template's own wording, so they stay visible and editable there.
"""

from __future__ import annotations

from app.core.config import settings
from app.intake import sow

FIELDS = ("consultancy_deliverables", "consultancy_fee_basis", "key_individuals", "company_contact")

ADVICE_TICK = "There are no separate deliverables (advice only)"
ADVICE_ONLY = (
    "No separate deliverables are specified. The Consultant's output is the reports and advice it "
    "gives under Clause 2.2, all of which are Work Product."
)
CONSULTANCY_HELD = (
    "Enter these answers on the request first: {answers}. The Engagement Schedule of the Consultancy "
    "draft is made from them, and this request does not have them yet."
)
# basis -> (the rate as a noun phrase, the unit it is charged per, what a unit means and what is not charged)
_RATE_BASIS = {
    "Day rate": ("a day rate", "day", (" A day is eight (8) hours; part days are charged pro rata. "
                                       "Travel time is not chargeable unless agreed in writing.")),
    "Hourly rate": ("an hourly rate", "hour", (" Part hours are charged pro rata. "
                                               "Travel time is not chargeable unless agreed in writing.")),
}


# --- small formats (dpa.py reuses them) -----------------------------------------------------------


def cell(raw) -> str:
    """A typed answer on one line: runs of whitespace become one space, the ends are trimmed."""
    return " ".join(str(raw if raw is not None else "").split())


def sentence(text: str) -> str:
    """The text with a full stop unless it already ends a sentence (and nothing for no text)."""
    return text if not text or text.endswith((".", "!", "?")) else text + "."


def and_join(items: list[str]) -> str:
    """``a`` / ``a and b`` / ``a, b and c``; nothing for no items."""
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def rows_of(fv: dict, key: str) -> list[dict]:
    """The rows stored under ``key`` in the order entered; anything that is not a row is not one."""
    raw = fv.get(key)
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def held_message(labels: list[str]) -> str:
    """The 409 text naming the questions still to answer, each in double quotes, in form order."""
    return CONSULTANCY_HELD.format(answers=and_join([f'"{label}"' for label in labels]))


# --- the four fields ------------------------------------------------------------------------------


def _advice_only(fv: dict) -> bool:
    ticks = fv.get("advice_only")
    return ADVICE_TICK in (ticks if isinstance(ticks, list) else [ticks])


def _names(fv: dict) -> list[str]:
    return [n for n in (cell(r.get("name")) for r in rows_of(fv, "key_individuals")) if n]


def _fee_basis(fv: dict) -> str:
    """One statement of the fee, never the bases as alternatives. A rate basis without a rate, or a
    fixed fee without a total, gives nothing (the hold stops that draft; this never invents a figure)."""
    currency = fv.get("currency") or settings.default_currency
    basis = fv.get("fee_basis")
    if basis == "Fixed fee":
        total = sow.total_value(fv)
        return "" if total is None else f"The Fees are a fixed fee of {sow.money(total, currency)} for the Services."
    if basis in _RATE_BASIS:
        # The rate is read as exact money the way the total is: half-up to the cent.
        rate = sow.total_value({"value": fv.get("fee_rate")})
        if rate is not None:
            noun, unit, terms = _RATE_BASIS[basis]
            return (f"The Fees are charged at {noun} of {sow.money(rate, currency)} per {unit} "
                    f"for time properly spent on the Services.{terms}")
    return ""


def consultancy_values(fv: dict | None, *, example: bool = False) -> dict[str, str]:
    """The four Consultancy fill-in fields for a request's answers. A missing answer fills with
    nothing, so a request of another kind, or a held one, gets empty strings. ``example=True``
    ignores the answers and returns the example reading (``EXAMPLE_VALUES``)."""
    if example:
        return dict(EXAMPLE_VALUES)
    fv = fv or {}
    if _advice_only(fv):
        deliverables = ADVICE_ONLY
    else:
        deliverables = "\n\n".join(
            f"D{i}. {sentence(cell(r.get('name')))} Due date: {sow.day(r.get('due'))}."
            for i, r in enumerate(rows_of(fv, "consultancy_deliverables"), 1)
        )
    name, role = cell(fv.get("company_contact_name")), cell(fv.get("company_contact_role"))
    return {
        "consultancy_deliverables": deliverables,
        "consultancy_fee_basis": _fee_basis(fv),
        "key_individuals": sentence(and_join(_names(fv))),
        "company_contact": sentence(f"{name}, {role}" if name and role else name),
    }


def record_facts(fv: dict | None) -> dict:
    """What the draft was filled from, for its record (FR-49)."""
    fv = fv or {}
    return {
        "deliverable_rows": 0 if _advice_only(fv) else len(rows_of(fv, "consultancy_deliverables")),
        "fee_basis": fv.get("fee_basis"),
        "key_individuals": len(_names(fv)),
    }


# --- the example reading --------------------------------------------------------------------------

# A complete example Consultancy request for the Templates page check and preview (FR-33). It shares no
# name, deliverable or amount with any acceptance-test request, so finding it in a real draft means the
# example leaked (AC-38). Its dates sit inside both sample terms (the check's from 2026-10-01 until
# terminated, the preview's until 2027-09-30). The scope and the ceiling it reads with are the SOW
# example's `services` and `fee_amount` (INR 1,200,000.00), which the Consultancy schedule reuses.
EXAMPLE_ANSWERS: dict = {
    "request_form": "new_agreement", "agreement_type": "Consultancy", "paper": "Our template",
    "start_date": "2026-10-01", "value": "1200000", "currency": "INR",
    "consultancy_deliverables": [
        {"name": "Portal discovery report", "due": "2026-12-15"},
        {"name": "Launch readiness assessment", "due": "2027-06-30"},
    ],
    "fee_basis": "Day rate", "fee_rate": "20000",
    "key_individuals": [{"name": "Lena Fischer"}, {"name": "Omar Haddad"}],
    "company_contact_name": "Kavita Joshi", "company_contact_role": "Head of Digital Products",
}
EXAMPLE_VALUES: dict[str, str] = consultancy_values(EXAMPLE_ANSWERS)
# What marks example content. A check's suggested wording that still holds one of these (after the
# fields are turned back into codes) must never be applied to our paper (FR-34).
EXAMPLE_MARKERS: tuple[str, ...] = (
    "Portal discovery report", "Launch readiness assessment",
    "Lena Fischer", "Omar Haddad", "Kavita Joshi", "Head of Digital Products", "20,000.00",
)
