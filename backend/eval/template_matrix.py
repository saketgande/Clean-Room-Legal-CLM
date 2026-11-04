"""Every answer combination a shipped template can be drafted with (spec 014, FR-1A / FR-2).

Pure and free: no database, no AI. ``combinations(key)`` yields each combination of the form's choices
that matters to the template's wording, together with the register rows (``DraftContext``) it is drafted
with; ``tests/test_alignment_<key>.py`` renders every one and checks it against the template's playbook.
The choices are read from ``agreement_forms.json`` through ``form_fields`` + ``options_for``, so an option
the form stops offering (or starts offering) changes the matrix by itself and the matrix never drafts an
answer the wizard would refuse.

``python -m eval.template_matrix`` prints the size per template and how long generating and filling it takes.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import replace
from itertools import product

from app.intake import sow
from app.intake.agreement_forms import form_fields, options_for
from app.intake.paper import EXAMPLE_CONTEXT, DraftContext

COMPANY, COUNTERPARTY, EFFECTIVE = "Aegis Pharma Ltd", "Globex Corporation", "2026-10-01"

CONTEXTS: dict[str, DraftContext] = {
    "registered": EXAMPLE_CONTEXT,                                     # Mumbai office, signatory, Indian party
    "bare": DraftContext(),                                            # no register data, no parent
    "foreign": DraftContext(counterparty_jurisdiction="United States"),  # foreign party, no addresses
}
_NO_PARENT = replace(EXAMPLE_CONTEXT, parent_title=None, parent_effective=None)

# The detailed example reading, not a one-liner: a real request holds a scope written to the form's 15-word
# minimum and more, and the AI sample read the one-sentence version as too high level (PF-19).
_SCOPE = sow.EXAMPLE_ANSWERS["scope"]
_ADVICE_ONLY = "There are no separate deliverables (advice only)"
_KINDS = {"msa": "Services (MSA)", "vendor": "Buying from a vendor", "saas": "Software or SaaS",
          "consultancy": "Consultancy", "nda": "NDA"}
_FORMS = {"sow": "sow", "dpa": "dpa"}  # every other template is a kind of the New agreement form

Variant = tuple[str, dict]  # a label for the combination name, and the answers it sets
Dim = list[Variant]


def _form_of(key: str) -> str:
    return _FORMS.get(key, "new_agreement")


def _base(key: str) -> dict:
    """What every request for this template holds whatever else is chosen."""
    if key in _FORMS:
        return {"request_form": key}
    return {"request_form": "new_agreement", "agreement_type": _KINDS[key], "paper": "Our template",
            "start_date": EFFECTIVE}


def _choices(key: str, field: str, answers: dict | None = None) -> list[str]:
    """The options the form offers for a select / multi-select question given these answers."""
    f = next(f for f in form_fields(_form_of(key)) if f.key == field)
    return [o["value"] for o in options_for(f, {**_base(key), **(answers or {})}) or []]


def _dim(field: str, values: list[str], *, name: str | None = None) -> Dim:
    return [(f"{name or field}={v}", {field: v}) for v in values]


def _select(key: str, field: str, only: list[str] | None = None) -> Dim:
    return _dim(field, only or _choices(key, field))


def _yes_no(key: str, field: str) -> Dim:
    return _dim(field, _choices(key, field))


def _term(key: str) -> Dim:
    """Fixed end date; no end date; and renews automatically for each renewal length and notice period."""
    out: Dim = []
    for t in _choices(key, "term"):
        if t == "Renews automatically":
            for rt, nd in product(_choices(key, "renewal_term", {"term": t}), _choices(key, "notice_days", {"term": t})):
                out.append((f"term={t}/{rt}/{nd}", {"term": t, "end_date": "2027-09-30",
                                                    "renewal_term": rt, "notice_days": nd}))
        elif t == "Fixed end date":
            out.append((f"term={t}", {"term": t, "end_date": "2027-09-30"}))
        else:
            out.append((f"term={t}", {"term": t}))
    return out


def _value(*, given_blank: bool = True) -> Dim:
    return ([("value=given", {"value": "4500000"}), ("value=blank", {"value": ""})] if given_blank
            else [("value=given", {"value": "4500000"})])


def _ctx(names: tuple[str, ...]) -> Dim:
    return [(f"ctx={n}", {"__ctx": CONTEXTS[n]}) for n in names]


def _dims(key: str) -> tuple[dict, list[Dim]]:
    """The base answers and the dimensions of the full product for this template."""
    base = _base(key)

    def cur() -> Dim:
        return _select(key, "currency")

    if key == "nda":
        kinds: Dim = [("mutual", {"nda_kind": "Mutual"}),
                      *[(f"one-way/{d}", {"nda_kind": "One-way", "nda_direction": d})
                        for d in _choices(key, "nda_direction", {"nda_kind": "One-way"})]]
        laws = _dim("governing_law", [*_choices(key, "governing_law"), "", "Maharashtra, India"])
        return base, [kinds, _select(key, "nda_term"), laws, _yes_no(key, "personal_data"),
                      _ctx(("registered", "bare", "foreign"))]
    if key == "msa":
        return base, [_select(key, "payment_terms"), _term(key), _yes_no(key, "personal_data"),
                      _yes_no(key, "gxp"), cur(), _value(), _ctx(("registered", "bare", "foreign"))]
    if key == "vendor":
        return base, [_select(key, "payment_terms"), _select(key, "buying"), _term(key),
                      _yes_no(key, "personal_data"), cur(), _ctx(("registered", "bare", "foreign"))]
    if key == "saas":
        return base, [_select(key, "payment_terms"), _select(key, "hosting"), _term(key),
                      _yes_no(key, "personal_data"), cur(), _ctx(("registered", "bare", "foreign"))]
    if key == "consultancy":
        advice: Dim = [("deliverables=rows", {"consultancy_deliverables": [
            {"name": "Portal discovery report", "due": "2026-12-15"},
            {"name": "Launch readiness assessment", "due": "2027-06-30"}]}),
            ("deliverables=advice", {"advice_only": [_ADVICE_ONLY]})]
        base |= {"scope": _SCOPE, "key_individuals": [{"name": "Lena Fischer"}], "fee_rate": "20000",
                 "company_contact_name": "Kavita Joshi", "company_contact_role": "Head of Digital Products",
                 "value": "1200000"}  # the form requires it; it is the Fees cap in the Engagement Schedule
        return base, [_select(key, "payment_terms"), _select(key, "fee_basis"), advice, _term(key),
                      _yes_no(key, "personal_data"), cur(), _ctx(("registered", "foreign"))]
    if key == "sow":
        rows = [("Current-state assessment", "2026-12-15"), ("Pilot release", "2027-03-31"),
                ("Launch and handover pack", "2027-06-30")]
        shares = ("25.00", "50.00", "25.00")
        base |= {"sow_title": "Customer portal redesign", "scope": _SCOPE, "services_start": EFFECTIVE,
                 "services_end": "2027-09-30", "value": "1200000", "cp_signer_email": "jordan.lee@globex.example",
                 "deliverable_rows": [{"name": n, "due": d, "their_owner": "Anika Rao", "our_owner": "Meera Iyer"}
                                      for n, d in rows]}
        pricing: Dim = []
        for p in _choices(key, "pricing"):
            upd: dict = {"pricing": p}
            if p == "Milestones":
                upd["deliverable_rows"] = [{**r, "share": s} for r, s in zip(base["deliverable_rows"], shares, strict=True)]
            pricing.append((f"pricing={p}", upd))
        signer: Dim = [("signer=named", {"cp_signer_name": "Jordan Lee, Delivery Lead"}),
                       ("signer=blank", {"cp_signer_name": ""})]
        parent: Dim = [("parent=chosen", {"__ctx": EXAMPLE_CONTEXT}), ("parent=none", {"__ctx": _NO_PARENT})]
        return base, [pricing, _yes_no(key, "personal_data"), _yes_no(key, "gxp"), cur(), parent, signer]
    if key == "dpa":
        base |= {"our_role": "Controller: we decide how the data is used",
                 "processing_purpose": "Running the occupational-health clinic booking system for staff."}

        def singles(field: str) -> Dim:
            opts = _choices(key, field)
            return [*_dim(field, [[o] for o in opts], name=field), (f"{field}=all", {field: opts})]

        transfer: Dim = [("transfer=No", {"transfer": "No"}),
                         ("transfer=Yes", {"transfer": "Yes", "transfer_countries": [
                             {"country": "Ireland"}, {"country": "United States"}]})]
        subs: Dim = [("subprocessors=No", {"subprocessors": "No"}),
                     ("subprocessors=Yes", {"subprocessors": "Yes", "subprocessor_rows": [
                         {"name": "Northwind Hosting Ltd", "country": "Ireland", "processing": "Hosting"}]})]
        parent = [("parent=chosen", {"__ctx": EXAMPLE_CONTEXT}), ("parent=none", {"__ctx": _NO_PARENT})]
        return base, [singles("data_subjects"), singles("data_types"), transfer, subs, parent]
    raise KeyError(key)


def combinations(key: str) -> Iterator[tuple[str, dict, DraftContext, str, str]]:
    """(label, answers, register rows, our entity, their name) for each combination of this template."""
    base, dims = _dims(key)
    for picked in product(*dims):
        fields = dict(base)
        for _, upd in picked:
            fields |= upd
        ctx = fields.pop("__ctx", None) or CONTEXTS["registered"]
        yield f"{key}|" + "|".join(label for label, _ in picked), fields, ctx, COMPANY, COUNTERPARTY


def render(key: str, fields: dict, ctx: DraftContext, *, company: str = COMPANY, counterparty: str = COUNTERPARTY,
           body: str | None = None) -> str:
    """The draft for one combination: the shipped wording unless ``body`` is given."""
    from app.intake.drafting import render_document

    return render_document(key, company=company, counterparty=counterparty, effective=EFFECTIVE,
                           fields=fields, body=body, context=ctx)


def _pick(key: str, *parts: str) -> tuple[str, dict, DraftContext, str, str]:
    """The one combination whose label holds every part (exact ``dimension=value`` pieces)."""
    found = [c for c in combinations(key) if set(parts) <= set(c[0].split("|"))]
    assert len(found) >= 1, (key, parts)
    return found[0]


# The 19 combinations the deliberate AI sample reads (FR-1A), chosen to cover each template's variants.
_MAIN_PARTS = (
    ("nda", "mutual", "nda_term=3 years", "governing_law=India", "personal_data=No", "ctx=registered"),
    ("nda", "one-way/We share", "nda_term=2 years", "governing_law=England and Wales", "personal_data=Yes",
     "ctx=registered"),
    ("nda", "one-way/They share", "nda_term=5 years", "governing_law=Delaware", "personal_data=No", "ctx=foreign"),
    ("msa", "payment_terms=45 days", "term=Fixed end date", "currency=INR", "personal_data=No", "gxp=No",
     "value=given", "ctx=registered"),
    ("msa", "payment_terms=60 days", "term=Renews automatically/2 years/30 days", "currency=USD",
     "personal_data=Yes", "gxp=Yes", "value=given", "ctx=foreign"),
    ("sow", "pricing=Fixed fee", "currency=INR", "personal_data=No", "gxp=No", "parent=chosen", "signer=named"),
    ("sow", "pricing=Milestones", "currency=USD", "personal_data=Yes", "gxp=No", "parent=chosen", "signer=named"),
    ("sow", "pricing=Time and materials", "currency=INR", "personal_data=No", "gxp=No", "parent=none",
     "signer=blank"),
    ("vendor", "buying=Goods", "payment_terms=45 days", "currency=INR", "personal_data=No", "term=Fixed end date",
     "ctx=registered"),
    ("vendor", "buying=Services", "payment_terms=60 days", "term=Renews automatically/1 year/30 days",
     "personal_data=Yes", "currency=INR", "ctx=registered"),
    ("vendor", "buying=Goods and services", "payment_terms=90 days", "currency=EUR", "personal_data=No",
     "term=Fixed end date", "ctx=foreign"),
    ("saas", "hosting=India", "payment_terms=45 days", "term=Renews automatically/1 year/30 days", "currency=INR",
     "personal_data=No", "ctx=registered"),
    ("saas", "hosting=European Union", "payment_terms=60 days", "term=Fixed end date", "personal_data=Yes",
     "currency=EUR", "ctx=registered"),
    ("saas", "hosting=United States", "payment_terms=90 days", "term=Renews automatically/1 year/90 days",
     "currency=USD", "personal_data=No", "ctx=foreign"),
    ("saas", "hosting=Several regions", "payment_terms=45 days", "term=No end date (evergreen)", "currency=GBP",
     "personal_data=No", "ctx=registered"),
    ("consultancy", "fee_basis=Fixed fee", "deliverables=advice", "payment_terms=45 days", "personal_data=No",
     "currency=INR", "term=Fixed end date", "ctx=registered"),
    ("consultancy", "fee_basis=Day rate", "deliverables=rows", "payment_terms=60 days", "personal_data=Yes",
     "currency=USD", "term=Fixed end date", "ctx=foreign"),
    ("dpa", "data_subjects=['Employees']", "data_types=['Contact and identity']", "transfer=No",
     "subprocessors=No", "parent=none"),
    ("dpa", "data_subjects=all", "data_types=all", "transfer=Yes", "subprocessors=Yes", "parent=chosen"),
)
MAIN = [_pick(key, *parts) for key, *parts in _MAIN_PARTS]

KEYS = ("nda", "msa", "sow", "vendor", "saas", "consultancy", "dpa")


def main() -> None:
    total = 0
    for key in KEYS:
        t0 = time.perf_counter()
        n = sum(1 for _ in combinations(key))
        t1 = time.perf_counter()
        for _, fields, ctx, company, cp in combinations(key):
            render(key, fields, ctx, company=company, counterparty=cp)
        print(f"{key:12} {n:6} combinations  generated in {t1 - t0:5.2f}s  rendered in {time.perf_counter() - t1:5.2f}s")
        total += n
    print(f"{'total':12} {total:6}")


if __name__ == "__main__":
    main()
