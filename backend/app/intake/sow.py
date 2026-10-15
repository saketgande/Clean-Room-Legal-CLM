"""What the Statement of Work template is filled with from a request's answers (spec 007).

Pure functions, no database and no audit: the nine SOW fill-in fields (``sow_values``), the
payment arithmetic (``split_payments``), the example reading the Templates page check and preview
use, and the hold message. It imports only the stdlib and settings so that both
``intake.drafting`` and ``drafting_templates.service`` can import it without a cycle.

Money is exact ``Decimal`` end to end. A float anywhere here would make "the payments add up
exactly to the total" a coincidence of the amounts rather than a property of the code.

A real request is filled only from its own answers, and an answer it lacks fills with nothing.
Example content (``EXAMPLE_VALUES``) is returned only when a caller passes ``example=True``, which
only the Templates page check does (FR-24): a draft for a request must never contain it.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.config import settings

FIELDS = (
    "sow_title", "services", "deliverables_list", "key_personnel_list", "acceptance_list",
    "pricing_model", "fee_amount", "milestone_payments_list", "provider_representative",
)
# The fields that are built from the request's deliverable rows. A wording that uses none of them
# does not need the rows, so an older request (free-text deliverables only) drafts from it as before.
ROW_FIELDS = ("deliverables_list", "key_personnel_list", "acceptance_list", "milestone_payments_list")

DRAFT_HELD = (
    "Enter the deliverables as rows first. This Statement of Work request has its deliverables only as "
    "free text, from before the form asked for a table, and the draft is made from the rows (name, due "
    "date, their owner and our owner, and a share of the fee when pricing is Milestones). The earlier "
    "text stays on the request to copy from."
)
HELD_STEP_NOTE = DRAFT_HELD + " This step carries on by itself once the rows are saved."
HELD_STEP_NOT_MANUAL = (
    "This step makes the Statement of Work draft from the request's deliverable rows, so it can't be "
    "marked done by hand. Enter the rows on the request and the step carries on by itself."
)
TM_NO_MILESTONES = (
    "No fixed milestone payments apply, because this SOW is priced on a time-and-materials basis. "
    "Payment follows Clause 9.2, including its hold-back until the Customer has accepted the "
    "Deliverables in writing."
)
_PRICING_MODEL = {
    "Fixed fee": "Fixed fee. The Fees for this SOW are the fixed fee stated in Clause 8.2.",
    "Time and materials": (
        "Time and materials. The Fees for this SOW are charged on a time-and-materials basis under "
        "Clause 8.3 and may not exceed the not-to-exceed amount stated there."
    ),
    "Milestones": (
        "Fixed fee payable in instalments. The Fees for this SOW are the fixed fee stated in Clause 8.2, "
        "payable in the instalments set out in Clause 9.1, each only after the Customer has accepted in "
        "writing the Deliverable it is linked to."
    ),
}
_CENT = Decimal("0.01")


# --- small formats ------------------------------------------------------------------------------


def day(iso) -> str:
    """A date as a contract writes it ("30 November 2026"); anything that is not a date as written."""
    try:
        d = date.fromisoformat(str(iso).strip()[:10])
    except ValueError:
        return str(iso)
    return f"{d.day} {d:%B %Y}"


def money(amount: Decimal, currency: str) -> str:
    """"INR 4,500,000.00": the currency, grouping with a comma, two decimals."""
    return f"{currency} {amount:,.2f}"


def _cell(raw) -> str:
    return " ".join(str(raw if raw is not None else "").split())


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else text + "."


def _percent(raw) -> str:
    """A stored share ("40.00", "12.50") without its trailing zeros ("40", "12.5")."""
    return f"{Decimal(str(raw)).normalize():f}"


def _numbers(ids: list[int]) -> str:
    labels = [f"D{i}" for i in ids]
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} and {labels[-1]}"


def rows_of(fv: dict | None) -> list[dict]:
    """The request's deliverable rows in the order entered (that order sets D1, D2, ...)."""
    raw = (fv or {}).get("deliverable_rows")
    return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []


def total_value(fv: dict) -> Decimal | None:
    """The request's total value as exact money (half-up to two decimals), or None when it isn't one."""
    raw = fv.get("value")
    try:
        total = Decimal(str("" if raw is None else raw).replace(",", "").strip()).quantize(_CENT, ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return total if total.is_finite() else None


# --- the arithmetic -----------------------------------------------------------------------------


def split_payments(total: Decimal, n: int, shares: list[Decimal] | None = None) -> list[Decimal]:
    """``n`` payments that add up to ``total`` exactly: equal parts, or each row's share (a percentage).

    The total is rounded half-up to the cent first (the only way payments can add up exactly when a
    total carries more decimals), each payment but the last is rounded half-up, and the last takes
    whatever is left, so the rounding difference is added to it or taken from it.
    """
    # ponytail: with a total under about n cents the last payment can come out 0.00 or below. The
    # form refuses a total of 0 or less and real SOWs are far above this, so there is no guard.
    total = total.quantize(_CENT, ROUND_HALF_UP)
    raw = [total / n] * n if shares is None else [total * s / 100 for s in shares]
    paid = [p.quantize(_CENT, ROUND_HALF_UP) for p in raw[:-1]]
    return [*paid, total - sum(paid, Decimal(0))]


# --- the nine fields ----------------------------------------------------------------------------


def sow_values(fv: dict | None, *, example: bool = False) -> dict[str, str]:
    """The nine SOW fill-in fields for a request's answers. A missing answer fills with nothing, so a
    request that is not a SOW, or has no rows, gets empty strings. ``example=True`` ignores the answers
    and returns the example reading (``EXAMPLE_VALUES``)."""
    if example:
        return dict(EXAMPLE_VALUES)
    fv = fv or {}
    rows = rows_of(fv)
    currency = fv.get("currency") or settings.default_currency
    total = total_value(fv)
    pricing = str(fv.get("pricing") or "")
    names = [_cell(r.get("name")) for r in rows]

    owners: dict[str, tuple[str, list[int]]] = {}
    for i, r in enumerate(rows, 1):
        owner = _cell(r.get("their_owner"))
        owners.setdefault(owner.lower(), (owner, []))[1].append(i)
    key_personnel = [
        f"{owner} - owner of {_numbers(ids)} - delivery of {'that Deliverable' if len(ids) == 1 else 'those Deliverables'}"
        for owner, ids in owners.values() if owner
    ]

    payments = ""
    if pricing == "Time and materials":
        payments = TM_NO_MILESTONES
    elif pricing in ("Fixed fee", "Milestones") and rows and total is not None:
        shares = [Decimal(str(r.get("share"))) for r in rows] if pricing == "Milestones" else None
        entries = []
        for i, paid in enumerate(split_payments(total, len(rows), shares)):
            amount = money(paid, currency)
            if shares is not None:
                amount = f"{_percent(shares[i])}% of the fixed fee: {amount}"
            entries.append(
                f"M{i + 1}: Acceptance of D{i + 1} ({names[i]}). Payment: {amount}, payable only after "
                f"the Customer has accepted D{i + 1} in writing."
            )
        payments = "\n\n".join(entries)

    signer, email = _cell(fv.get("cp_signer_name")), _cell(fv.get("cp_signer_email"))
    representative = ""
    if signer:
        representative = f"{signer} ({email}), authorised signatory" if email else f"{signer}, authorised signatory"

    return {
        "sow_title": _cell(fv.get("sow_title")),
        "services": str(fv.get("scope") or "").replace("\r\n", "\n").strip(),
        "deliverables_list": "\n\n".join(
            f"D{i}. {_sentence(names[i - 1])} Due date: {day(r.get('due'))}. "
            f"Provider owner: {_sentence(_cell(r.get('their_owner')))} "
            f"Customer owner: {_sentence(_cell(r.get('our_owner')))}"
            for i, r in enumerate(rows, 1)
        ),
        "key_personnel_list": "\n\n".join(key_personnel),
        "acceptance_list": "\n\n".join(f"D{i}: {_sentence(n)}" for i, n in enumerate(names, 1)),
        "pricing_model": _PRICING_MODEL.get(pricing, ""),
        "fee_amount": money(total, currency) if total is not None else "",
        "milestone_payments_list": payments,
        "provider_representative": representative,
    }


# --- the example reading ------------------------------------------------------------------------

# A complete example request for the Templates page check and preview (FR-23). It shares no name,
# deliverable or amount with any acceptance-test request, so finding it in a real draft means the
# example leaked (AC-27). Its dates sit inside the sample term of the preview (2026-10-01 to
# 2027-09-30), and the payments (300,000.00, 600,000.00, 300,000.00) add up to the 1,200,000.00 fee.
EXAMPLE_ANSWERS: dict = {
    "request_form": "sow",
    "sow_title": "Customer portal redesign",
    "scope": ("Redesign and rebuild the customer self-service portal, including user research, visual "
              "design, build, testing and launch support."),
    "services_start": "2026-10-01", "services_end": "2027-09-30",
    "value": "1200000", "currency": "INR", "pricing": "Milestones",
    "cp_signer_name": "Jordan Lee", "cp_signer_email": "jordan.lee@globex.example",
    "deliverable_rows": [
        {"name": "Current-state assessment", "due": "2026-12-15", "their_owner": "Anika Rao",
         "our_owner": "Meera Iyer", "share": "25.00"},
        {"name": "Pilot release", "due": "2027-03-31", "their_owner": "Anika Rao",
         "our_owner": "Meera Iyer", "share": "50.00"},
        {"name": "Launch and handover pack", "due": "2027-06-30", "their_owner": "Daniel Okafor",
         "our_owner": "Meera Iyer", "share": "25.00"},
    ],
}
EXAMPLE_VALUES: dict[str, str] = sow_values(EXAMPLE_ANSWERS)
# What marks example content. A check's suggested wording that still holds one of these (after the
# fields are turned back into codes) must never be applied to our paper (FR-24).
EXAMPLE_MARKERS: tuple[str, ...] = (
    "Current-state assessment", "Pilot release", "Launch and handover pack",
    "Anika Rao", "Daniel Okafor", "Meera Iyer", "Jordan Lee", "jordan.lee@globex.example",
    "Customer portal redesign", EXAMPLE_ANSWERS["scope"],
    "1,200,000.00", "600,000.00", "300,000.00",
)
