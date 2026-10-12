"""A form must not accept an answer that cannot be right (round 2, bug 7).

The MSA form took an end date before its start date and put both into clause 7.1; the signer's email
was accepted with no "@". Both are now refused by the server, which the wizard also mirrors in the
browser. These tests call the same validator filing and updating call, with no database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.intake.agreement_forms import form_defs, form_fields
from app.intake.service import _validate_field_values, looks_like_email

MSA = {
    "request_form": "new_agreement", "agreement_type": "Services (MSA)", "entity": "Us Ltd", "entity_id": "e",
    "counterparty": "Them Ltd", "counterparty_id": "c", "cp_signer_name": "Maya Chen",
    "cp_signer_email": "maya@example.com", "paper": "Our template", "purpose": "Managed services",
    "department": "Procurement", "value": "100000", "currency": "INR", "start_date": "2026-10-01",
    "term": "Fixed end date", "end_date": "2027-09-30", "scope": "Hosting", "payment_terms": "60 days",
    "personal_data": "No", "gxp": "No",
}


def complete(form, **over):
    """Every question of a form answered with something plausible, so a test fails only on the rule it is about."""
    values = {"request_form": form}
    for f in form_fields(form):
        kind, options = f.kind, [o["value"] for o in (f.options or [])]
        values[f.key] = (
            options[:1] if kind == "multiselect" else options[0] if options else
            "2026-10-01" if kind == "date" else "10" if kind == "number" else "a@example.com" if kind == "email" else "x"
        )
    values.update(over)
    return values


def check(values, form="new_agreement"):
    return _validate_field_values(form_fields(form), dict(values))


def refused(values, form="new_agreement"):
    with pytest.raises(HTTPException) as exc:
        check(values, form)
    assert exc.value.status_code == 422
    return exc.value.detail


# --- dates --------------------------------------------------------------------

def test_a_complete_form_still_files():
    assert check(MSA)["end_date"] == "2027-09-30"


def test_an_end_date_before_the_start_date_is_refused_and_the_message_names_both():
    detail = refused({**MSA, "start_date": "2026-10-01", "end_date": "2026-01-01"})
    assert '"End date" cannot be before "Start date"' in detail


def test_an_end_date_on_the_start_date_is_allowed():
    assert check({**MSA, "end_date": "2026-10-01"})["end_date"] == "2026-10-01"


def test_a_statement_of_work_cannot_end_before_it_starts():
    sow = complete("sow", services_start="2026-11-01", services_end="2026-10-01")
    assert '"Services end" cannot be before "Services start"' in refused(sow, "sow")


def test_an_end_date_that_is_not_asked_for_is_not_compared():
    """An NDA has no end date: a stale one left over from another choice is dropped, not compared."""
    nda = {**MSA, "agreement_type": "NDA", "nda_kind": "Mutual", "nda_term": "2 years", "end_date": "2020-01-01"}
    nda = complete("new_agreement", **nda)
    assert "end_date" not in check(nda)


def test_the_regularise_form_has_the_same_rule():
    form = complete("regularize", start_date="2026-09-01", term="Fixed end date", end_date="2026-08-01")
    assert '"End date" cannot be before "Start date"' in refused(form, "regularize")


def test_every_not_before_rule_points_at_a_date_field_on_the_same_form():
    for form in form_defs():
        kinds = {f["key"]: f["kind"] for f in form["fields"]}
        for f in form["fields"]:
            if "not_before" in f:
                assert kinds[f["key"]] == "date"
                assert kinds[f["not_before"]] == "date", f"{form['key']}.{f['key']}"


# --- emails -------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["maya.example.com", "maya@", "@example.com", "maya@example", "ma ya@example.com",
                                 "maya@@example.com", "maya@exa..mple.com", "maya@example.", "a@b@c.com", "maya@.example.com"])
def test_an_email_that_cannot_be_delivered_is_refused(bad):
    assert "must be a full email address" in refused({**MSA, "cp_signer_email": bad})


@pytest.mark.parametrize("good", ["maya@example.com", " maya@example.com ", "maya.chen+legal@sub.example.co.in"])
def test_ordinary_addresses_are_kept_and_trimmed(good):
    assert check({**MSA, "cp_signer_email": good})["cp_signer_email"] == good.strip()


def test_every_signer_email_question_is_an_email_question():
    emails = [(f["key"], f["kind"]) for form in form_defs() for f in form["fields"] if f["key"].endswith("_email")]
    assert emails and all(kind == "email" for _, kind in emails)


def test_looks_like_email_refuses_an_address_longer_than_a_mailbox_can_be():
    assert not looks_like_email("a" * 250 + "@example.com")


def test_the_frontend_and_the_server_agree_the_json_is_where_the_rules_live():
    raw = json.loads(Path(__file__).resolve().parents[1].joinpath("app/intake/agreement_forms.json").read_text())
    assert any(f.get("not_before") for form in raw for f in form["fields"])
