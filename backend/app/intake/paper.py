"""What our own paper is filled with beyond the request's plain answers (spec 014).

Pure functions, no database and no audit: the governing law, the dispute route and its seat, both
signature blocks, the money minimums, the personal-data, hosting, vendor and consultancy wording
that depends on an answer, and the "is this scope placeholder text" check. Shaped like
``intake.sow``: it imports only the stdlib, settings and ``intake.sow`` (for its money helpers and
the two SOW fields), and ``sow`` never imports it, so ``intake.drafting`` and
``drafting_templates`` can both import it without a cycle.

A real request is filled only from its own answers and the register rows passed in ``context``;
what it lacks fills with ``BLANK`` (a line to write on) or a stated default, never an empty label
and never an invented address or name (FR-18). Example content (``EXAMPLE_CONTEXT``) is used only
when a caller passes ``example=True`` (the Templates-page check and preview).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings
from app.intake import sow

BLANK = "_" * 30

SOW_PRICING_HELD = (
    "Choose how this Statement of Work is priced (fixed fee, milestones, or time and materials) and "
    "enter its total value on the request first. The draft prints only the pricing chosen, so it is "
    "not made without it."
)
SCOPE_HELD = (
    "Describe the work on the request first, in at least 15 words. The scope it holds now reads as "
    "placeholder text, and the draft would print it as the services the other side must perform."
)

FEE_CAP_HELD = (
    "Enter the total contract value on the request first. The Consultancy draft caps the Fees at that "
    "amount and an advance at 20% of it, so it is not made without it."
)


@dataclass(frozen=True)
class DraftContext:
    entity_address: str | None = None             # LegalEntity.registered_address
    entity_signatory: str | None = None           # LegalEntity.authorised_signatory ("R. Iyer, Director")
    counterparty_jurisdiction: str | None = None  # Counterparty.jurisdiction
    counterparty_address: str | None = None       # Counterparty.address
    parent_title: str | None = None               # the request's parent contract (SOW, DPA)
    parent_effective: str | None = None           # its effective date, ISO


EXAMPLE_CONTEXT = DraftContext(
    entity_address="Level 5, One BKC, Bandra Kurla Complex, Mumbai 400051, India",
    entity_signatory="Priya Nair, Director",
    counterparty_jurisdiction="India",
    counterparty_address="12 MG Road, Bengaluru 560001, India",
    parent_title="Master Services Agreement", parent_effective="2026-04-01")
EXAMPLE_MARKERS = ("Priya Nair", "One BKC", "12 MG Road", "Bengaluru 560001")

FIELDS = ("dispute_seat", "dispute_resolution", "our_signatory_name", "our_signatory_title",
          "their_signatory_name", "their_signatory_title", "company_address", "counterparty_address",
          "fee_currency", "insurance_minimum", "public_liability_minimum", "data_breach_floor", "dpa_status", "nda_personal_data",
          "gxp_clause", "parent_agreement", "pricing_terms", "sow_payment", "supply_description",
          "vendor_supply_terms", "vendor_insurance", "hosting_clause", "renewal_notice_days",
          "consultancy_invoicing")

# The ONLY place these amounts are written (decision 3): Finance changes them here, in one edit.
# "five_crore" is the cyber / professional-indemnity / data-breach floor, "one_crore" the goods floor.
# No exchange-rate lookup: the non-INR rows are fixed equivalents.
MINIMUMS: dict[str, dict[str, str]] = {
    "INR": {"five_crore": "INR 50,000,000 (five crore rupees)", "one_crore": "INR 10,000,000 (one crore rupees)"},
    "USD": {"five_crore": "USD 600,000", "one_crore": "USD 120,000"},
    "EUR": {"five_crore": "EUR 550,000", "one_crore": "EUR 110,000"},
    "GBP": {"five_crore": "GBP 475,000", "one_crore": "GBP 95,000"},
}

_NDA_LAW = {
    "India": "India", "England and Wales": "England and Wales", "Singapore": "Singapore",
    "New York": "the State of New York", "Delaware": "the State of Delaware",
}

# ponytail: a fixed city list; upgrade = a seat question on the per-type forms (next feature).
_CITIES = {
    "Mumbai": "Mumbai", "New Delhi": "New Delhi", "Delhi": "New Delhi", "Bengaluru": "Bengaluru",
    "Bangalore": "Bengaluru", "Chennai": "Chennai", "Hyderabad": "Hyderabad", "Kolkata": "Kolkata",
    "Pune": "Pune", "Ahmedabad": "Ahmedabad", "Gurugram": "Gurugram", "Gurgaon": "Gurugram",
    "Noida": "Noida", "Chandigarh": "Chandigarh", "Kochi": "Kochi", "Jaipur": "Jaipur",
    "Lucknow": "Lucknow", "Indore": "Indore", "Bhubaneswar": "Bhubaneswar", "Coimbatore": "Coimbatore",
    "Thiruvananthapuram": "Thiruvananthapuram", "Nagpur": "Nagpur", "Vadodara": "Vadodara",
}
_CITY_RE = re.compile(r"\b(" + "|".join(sorted(_CITIES, key=len, reverse=True)) + r")\b", re.IGNORECASE)
_CITY_BY_LOWER = {k.lower(): v for k, v in _CITIES.items()}

_INDIAN_PLACES = (
    "India", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra",
    "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
)
_INDIAN_RE = re.compile(r"\b(" + "|".join(_INDIAN_PLACES) + r")\b", re.IGNORECASE)

# A foreign country named next to an Indian place word wins ("Punjab, Pakistan", "Hyderabad, Pakistan").
# ponytail: a short list of the countries that share place names with India or trade with it most; an
# unlisted country with a shared place name reads as Indian. Upgrade = a country field on the counterparty.
_FOREIGN_COUNTRIES = (
    "Pakistan", "Bangladesh", "Nepal", "Sri Lanka", "Bhutan", "Afghanistan", "Myanmar", "China", "Japan",
    "Singapore", "Malaysia", "Indonesia", "Thailand", "Philippines", "Vietnam", "Australia", "New Zealand",
    "United Arab Emirates", "UAE", "Saudi Arabia", "Qatar", "United Kingdom", "England", "Scotland",
    "Ireland", "Germany", "France", "Netherlands", "Switzerland", "Spain", "Italy", "United States", "USA",
    "Canada", "Mexico", "Brazil", "South Africa", "Kenya", "Mauritius",
)
_FOREIGN_RE = re.compile(r"\b(" + "|".join(_FOREIGN_COUNTRIES) + r")\b", re.IGNORECASE)

_ROLES = {  # doc type -> (the buyer, the provider, personal data as that template calls it)
    "msa": ("Customer", "Provider", "Personal Data"),
    "vendor": ("Company", "Vendor", "personal data"),
    "saas": ("Customer", "Provider", "personal data"),
    "consultancy": ("Company", "Consultant", "personal data"),
}
_ARBITRATED = ("msa", "vendor", "saas")

_SCOPE_TEST = re.compile(
    r"\bthis is (a |an |just a |for a )?test\b|\blorem\b|\bipsum\b|\btbd\b|\btba\b"
    r"|\bto be (decided|confirmed)\b", re.IGNORECASE)
_SCOPE_ONLY = {"test", "testing", "sample", "dummy"}

_INTERIM = "either Party may seek urgent interim or injunctive relief from any court of competent jurisdiction"

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


# --- small helpers ------------------------------------------------------------------------------


def _one_line(raw) -> str:
    return " ".join(str(raw if raw is not None else "").split())


def number_words(n: int) -> str:
    """30 -> "thirty (30)"; 45, 60, 90, 24 and 6 likewise (0 to 99). Longer than that (an older request's
    "120 days") is written as the figure alone rather than failing the whole draft."""
    if n >= 100:
        return f"{n} ({n})"
    if n < 20:
        word = _ONES[n]
    else:
        word = _TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else "")
    return f"{word} ({n})"


def _currency(fv: dict) -> str:
    cur = str(fv.get("currency") or settings.default_currency).strip().upper()
    return cur if cur in MINIMUMS else settings.default_currency


def _split_signer(raw) -> tuple[str, str]:
    """"R. Iyer, Director" -> ("R. Iyer", "Director"); a missing part is BLANK."""
    name, _, title = _one_line(raw).partition(",")
    return name.strip() or BLANK, title.strip() or BLANK


def is_foreign(jurisdiction: str | None) -> bool:
    """Non-blank and naming no Indian place (India, a state or territory, or a listed city), or naming a
    foreign country, which wins over a shared place word. Blank is Indian: the domestic default."""
    text = _one_line(jurisdiction)
    if not text:
        return False
    if _FOREIGN_RE.search(text):
        return True
    return not (_INDIAN_RE.search(text) or _CITY_RE.search(text))


def governing_law(doc_type: str, fv: dict) -> str:
    """NDA: its answer if it is one of the five offered, else India. Every other template: India."""
    if doc_type != "nda":
        return "India"
    return _NDA_LAW.get(str((fv or {}).get("governing_law") or "").strip(), "India")


def renewal_notice_days(fv: dict) -> int:
    """SaaS: the notice period asked for, never under 60 (one period in 6.1 and 6.2)."""
    digits = re.search(r"\d+", str((fv or {}).get("notice_days") or ""))
    return max(int(digits.group()) if digits else 60, 60)


def placeholder_scope(text: str | None) -> bool:
    """True when the scope reads as placeholder text: under 15 words, or a test / lorem / TBD phrase."""
    t = _one_line(text)
    # ponytail: a word list; upgrade = the form refusing it at filing.
    return len(t.split()) < 15 or bool(_SCOPE_TEST.search(t)) or t.lower().strip(" .") in _SCOPE_ONLY


def _seat(doc_type: str, company: str, ctx: DraftContext) -> str:
    if doc_type in _ARBITRATED and is_foreign(ctx.counterparty_jurisdiction):
        return "Singapore"
    # Indian addresses end city, state, PIN, so the last city named is ours ("Delhi Public School Road, Pune").
    found = _CITY_RE.findall(ctx.entity_address or "")
    if found:
        return _CITY_BY_LOWER[found[-1].lower()]
    return f"the city in which {company}'s registered office is situated"


def _dispute(doc_type: str, seat: str, foreign: bool, counterparty: str = "", jurisdiction: str | None = None) -> str:
    if doc_type == "nda":
        return (f"Each Party irrevocably submits to the exclusive jurisdiction of the courts at {seat} for any "
                "dispute arising out of or in connection with this Agreement and waives any objection to those "
                "courts, including that they are an inconvenient forum, provided that "
                f"{_INTERIM}.")
    if doc_type == "consultancy":  # every consultant, Indian or foreign (decision 2)
        return ("Any dispute that is not resolved by negotiation within that period is subject to the exclusive "
                f"jurisdiction of the courts at {seat}, provided that {_INTERIM}.")
    if doc_type not in _ARBITRATED:
        return ""
    if foreign:
        # The playbook allows a Singapore seat only for a party outside India, so the clause says so itself
        # rather than leaving a reader to infer it from the seat.
        place = _one_line(jurisdiction)
        status = (f"{counterparty} is incorporated or established in {place}, outside India. " if counterparty
                  else f"The other Party is incorporated or established in {place}, outside India. ")
        return (status + "Any dispute that is not resolved by negotiation within that period shall be finally resolved by "
                "arbitration by a sole arbitrator under the Arbitration Rules of the Singapore International "
                "Arbitration Centre in force when the arbitration starts. The seat of arbitration is Singapore, the "
                "language is English, and the award is final and binding on the Parties. Nothing in this clause "
                "prevents either Party from seeking urgent interim or injunctive relief from any court of competent "
                "jurisdiction, including under section 9 of the Arbitration and Conciliation Act, 1996.")
    return ("Any dispute that is not resolved by negotiation within that period shall be finally resolved by "
            "arbitration by a sole arbitrator under the Arbitration and Conciliation Act, 1996, administered by the "
            "Mumbai Centre for International Arbitration under its rules. "
            f"The seat of arbitration is {seat}, the language is English, and the award is final and binding on the "
            f"Parties. Subject to this, the courts at {seat} alone have supervisory jurisdiction over the arbitration, and "
            "nothing in this clause prevents either Party from seeking urgent interim or injunctive relief from any court of "
            "competent jurisdiction.")


def _dpa_status(doc_type: str, fv: dict) -> str:
    if doc_type not in _ROLES:
        return ""
    buyer, provider, pd = _ROLES[doc_type]
    if str(fv.get("personal_data") or "").strip() == "No":
        return (f"The Parties do not expect the {provider} to process {pd} under this Agreement. They shall "
                f"nonetheless sign a data processing agreement on the {buyer}'s standard template at the same time as "
                f"this Agreement; it applies from the Effective Date, forms part of this Agreement and prevails over it "
                f"on any processing of {pd}, and no {pd} is processed before it is signed.")
    return (f"The Parties expect the {provider} to process {pd} under this Agreement. They shall sign a data "
            f"processing agreement on the {buyer}'s standard template at the same time as this Agreement; it applies "
            f"from the Effective Date, forms part of this Agreement and prevails over it on the processing of {pd}, "
            f"and no {pd} is processed before it is signed.")


def _nda_personal_data(fv: dict) -> str:
    base = ("the Receiving Party shall process it only for the Purpose and in compliance with applicable data "
            "protection laws, including the Digital Personal Data Protection Act, 2023, with reasonable security "
            "safeguards")
    if str(fv.get("personal_data") or "").strip() == "Yes":
        # The breach notice is the template's own fixed sentence in 11.1, so it is not repeated here.
        return f"Where Confidential Information includes personal data, {base}."
    return ("The Parties do not expect to share personal data under this Agreement. "
            f"If any Confidential Information includes personal data, {base}.")


def _parent(ctx: DraftContext, doc_type: str) -> str:
    if ctx.parent_title:
        dated = f" dated {sow.day(ctx.parent_effective)}" if ctx.parent_effective else ""
        return f'the agreement titled "{ctx.parent_title}"{dated} between the Parties'
    return ("the services agreement between the Parties" if doc_type == "dpa"
            else "the master agreement between the Parties")


def _supply_kind(fv: dict) -> str:
    return {"Goods": "goods", "Services": "services"}.get(str(fv.get("buying") or "").strip(), "goods and services")


_GOODS_TERMS = (
    "\n\n3.3 Delivery. Goods shall be delivered DAP (Incoterms 2020) to the place stated in the Order, properly "
    "packed and labelled, with a delivery note identifying the Order. Title and risk in the goods pass to the "
    "Company on acceptance.\n\n3.4 Quantities. The Company may reject any delivery that is short of, or in excess "
    "of, the quantity ordered; excess goods are held at the Vendor's risk and returned at its cost.\n\n3.5 Recall. "
    "If any goods become subject to a recall or a safety notice, the Vendor shall notify the Company at once, carry "
    "out the recall, and replace or refund the affected goods, at its own cost."
)


def _services_terms(number: str) -> str:
    return (f"\n\n{number} Services. The Vendor shall perform services with the reasonable skill, care and diligence "
            "of a competent supplier of similar services, by suitably qualified and experienced personnel, and shall "
            "meet or exceed any service levels set out in the applicable Order; any service credits are a price "
            "adjustment and are without prejudice to the Company's other rights. Services are accepted only on the "
            "Company's written confirmation that they meet the Specification, and until then the Vendor shall "
            "re-perform them at no charge.")


def _vendor_supply_terms(kind: str) -> str:
    if kind == "goods":
        return _GOODS_TERMS
    if kind == "services":
        return _services_terms("3.3")
    return _GOODS_TERMS + _services_terms("3.6")


def _vendor_insurance(kind: str, insurance: str, goods_floor: str) -> str:
    covers = []
    if kind in ("goods", "goods and services"):
        covers.append("public and product liability insurance with a limit of not less than the greater of the "
                      f"total Fees under this Agreement and {goods_floor} per claim")
    if kind in ("services", "goods and services"):
        covers.append(f"professional indemnity insurance with a limit of not less than {insurance} per claim and "
                      "in the aggregate")
    covers.append("employer's liability or workmen's compensation insurance as required by Applicable Law")
    body = (", ".join(covers[:-1]) + ", and " + covers[-1]) if len(covers) > 2 else " and ".join(covers)
    return ("The Vendor shall maintain with reputable insurers, during the term of this Agreement and for one (1) "
            f"year after it ends, {body}. Where the Vendor processes the Company's personal data or has access to "
            f"its systems, it shall also maintain cyber liability insurance of not less than {insurance} per claim. "
            "The Vendor shall provide evidence of cover on request.")


_HOSTING_TAIL = (
    "\n\n9.2 The Provider shall not move Customer Data to, or permit access to it from, any other location without "
    "the prior written approval of the Customer's privacy function, never to a "
    "country or territory restricted by the Central Government under section 16 of the Digital Personal Data "
    "Protection Act, 2023, and, for personal data subject to the GDPR or the UK GDPR, only under the Standard "
    "Contractual Clauses or another safeguard recognised by that law."
)
_APPROVAL = (". That location shall be stated in the Order Form and is subject to the written approval of the "
             "Customer's privacy function, which shall be given before any Customer Data is stored there.")


def _hosting(fv: dict) -> str:
    where = str(fv.get("hosting") or "").strip()
    lead = "9.1 The Provider shall host, store and process Customer Data only in data centres located in"
    if where == "European Union":
        first = f"{lead} the European Union{_APPROVAL}"
    elif where == "United States":
        first = f"{lead} the United States of America{_APPROVAL}"
    elif where == "Several regions":
        first = ("9.1 The Provider shall host, store and process Customer Data only in the data centre regions listed "
                 "in the Order Form signed with this Agreement, and in no other region. Each listed region is subject "
                 "to the written approval of the Customer's privacy function, which shall be given before any Customer "
                 "Data is stored there.")
    else:
        first = f"{lead} India."
    return first + _HOSTING_TAIL


def _consultancy_invoicing(fv: dict) -> str:
    basis = fv.get("fee_basis")
    if basis == "Fixed fee":
        rows = fv.get("consultancy_deliverables")
        ticks = fv.get("advice_only")
        advice = "There are no separate deliverables (advice only)" in (ticks if isinstance(ticks, list) else [ticks])
        if isinstance(rows, list) and rows and not advice:
            return ("The Consultant shall invoice the fixed fee in equal parts, each on the Company's written "
                    "acceptance of a Deliverable listed in the Engagement Schedule.")
        return ("The Consultant shall invoice the fixed fee monthly in arrears, in proportion to the Services "
                "performed in that month.")
    if basis in ("Day rate", "Hourly rate"):
        return "The Consultant shall invoice the Fees monthly in arrears for time properly spent in that month."
    return "The Consultant shall invoice the Company monthly in arrears."


# --- the fields ---------------------------------------------------------------------------------


def paper_values(doc_type: str, fv: dict, *, company: str, counterparty: str,
                 context: DraftContext | None, example: bool = False) -> dict[str, str]:
    """All 23 ``FIELDS`` for a request's answers and register rows. ``example=True`` uses
    ``EXAMPLE_CONTEXT`` when ``context`` is None (the Templates-page check and preview only)."""
    fv = fv or {}
    ctx = context if context is not None else (EXAMPLE_CONTEXT if example else DraftContext())
    cur = settings.default_currency if doc_type == "dpa" else _currency(fv)
    mins = MINIMUMS[cur]
    foreign = is_foreign(ctx.counterparty_jurisdiction)
    seat = _seat(doc_type, company, ctx)
    ours = _split_signer(ctx.entity_signatory)
    theirs = _split_signer(fv.get("cp_signer_name"))
    kind = _supply_kind(fv)
    is_sow = doc_type == "sow"
    sow_fv = sow.EXAMPLE_ANSWERS if example and is_sow else fv
    # pricing_terms / sow_payment are built in intake.sow (the SOW template's task); until they exist
    # the SOW fields fill with nothing rather than failing here.

    def sow_field(name: str) -> str:
        return str(getattr(sow, name)(sow_fv)) if is_sow and hasattr(sow, name) else ""

    return {
        "dispute_seat": seat,
        "dispute_resolution": _dispute(doc_type, seat, foreign, counterparty, ctx.counterparty_jurisdiction),
        "our_signatory_name": ours[0], "our_signatory_title": ours[1],
        "their_signatory_name": theirs[0], "their_signatory_title": theirs[1],
        "company_address": _one_line(ctx.entity_address) or BLANK,
        "counterparty_address": _one_line(ctx.counterparty_address) or BLANK,
        "fee_currency": cur,
        "insurance_minimum": mins["five_crore"],
        "public_liability_minimum": mins["one_crore"],
        "data_breach_floor": mins["five_crore"],
        "dpa_status": _dpa_status(doc_type, fv),
        "nda_personal_data": _nda_personal_data(fv),
        "gxp_clause": ("\n\n3.4 Where the Services are GxP-regulated, the Parties shall sign a quality agreement "
                       "before the Services start, and it prevails over this Agreement on quality matters."
                       if doc_type == "msa" and str(fv.get("gxp") or "").strip() == "Yes" else ""),
        "parent_agreement": _parent(ctx, doc_type),
        "pricing_terms": sow_field("pricing_terms"),
        "sow_payment": sow_field("sow_payment"),
        "supply_description": kind,
        "vendor_supply_terms": _vendor_supply_terms(kind) if doc_type == "vendor" else "",
        "vendor_insurance": _vendor_insurance(kind, mins["five_crore"], mins["one_crore"]) if doc_type == "vendor" else "",
        "hosting_clause": _hosting(fv) if doc_type == "saas" else "",
        "renewal_notice_days": number_words(renewal_notice_days(fv)),
        "consultancy_invoicing": _consultancy_invoicing(fv) if doc_type == "consultancy" else "",
    }
