"""Renewal dates counted by code from the AI's typed wording (app/renewals/service.py).
A wrong expiry or notice deadline here is a contract that rolls over (or closes)
without anyone deciding, so each test names the slip it guards."""

import asyncio
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.ai.schemas import RenewalExtractionOutput
from app.contracts.models import Contract
from app.core.deadlines import Anchor
from app.renewals import recommendation
from app.renewals import service as renewals
from app.renewals.models import RenewalEvent

HOOLI = {
    "term_kind": "fixed", "auto_renews": True,
    "initial_term": {"number": 1, "unit": "years", "counted_from": "effective_date"}, "citations": [{"quote": "an initial term of 1 (1) year(s) from the Effective Date"}],
    "renewal_term": {"number": 1, "unit": "years"},
    "notice_terms": [{"kind": "non_renewal", "number": 90, "unit": "days", "quote": "ninety (90) days"}],
}


def _facts(**over):
    return RenewalExtractionOutput.model_validate({**HOOLI, **over})


def _anchors(effective=None, expiry=None, expiry_source=None, conflict=None):
    return {
        "effective_date": Anchor("effective_date", effective, "ai" if effective else None, conflict),
        "expiration_date": Anchor("expiration_date", expiry, expiry_source, None),
        "signature_date": Anchor("signature_date", None, None, None),
    }


# --- renewal_dates: pure ------------------------------------------------------


def test_a_one_year_auto_renewing_term_gets_every_date_with_its_formula():
    """Guards the AI's own arithmetic coming back: a one-year term from 15 Oct 2026
    ends on the anniversary, notice is counted back from it (and stays on its
    Saturday), the window opens 30 days before that, and the next term is shown."""
    block = renewal_dates_for(_facts(), effective=date(2026, 10, 15))
    assert {k: block[k] for k in renewals._DATE_KEYS} == {
        "expiration_date": "2027-10-15", "notice_date": "2027-07-17",
        "window_opens": "2027-06-17", "next_expiration_date": "2028-10-15",
    }
    assert block["formulas"] == {
        "expiration_date": "expires Fri 15 Oct 2027 = effective date Thu 15 Oct 2026 + 1 year",
        "notice_date": "notice by Sat 17 Jul 2027 = expiry Fri 15 Oct 2027 - 90 days",
        "window_opens": "window opens Thu 17 Jun 2027 = notice by Sat 17 Jul 2027 - 30 days",
        "next_expiration_date": "next term ends Sun 15 Oct 2028 = expiry Fri 15 Oct 2027 + 1 year",
    }
    assert block["reason"] is None


def renewal_dates_for(facts, **anchors):
    return renewals.renewal_dates(facts, _anchors(**anchors))


@pytest.mark.parametrize("facts, anchors, expiry, reason, guards", [
    (_facts(term_kind="evergreen", initial_term=None, auto_renews=None), {},
     None, "no end date - continues until terminated", "an evergreen contract given an invented end date"),
    (_facts(stated_end_date="2028-03-31", stated_end_date_quote="shall end on 31st March, 2028"),
     {"effective": date(2026, 10, 15)},
     "2028-03-31", None, "a written end date losing to the term arithmetic"),
    (_facts(stated_end_date="2028-03-31", stated_end_date_quote="for a period of one (1) year"),
     {"effective": date(2026, 10, 15)},
     "2027-10-15", None, "an end date the model worked out, not in its quote, taken as written"),
    (_facts(stated_end_date="2028-03-31"), {"effective": date(2026, 10, 15)},
     "2027-10-15", None, "an end date with no quote at all taken as written"),
    (_facts(stated_end_date="2028-03-31", stated_end_date_quote="ends on 31 March 2028"),
     {"expiry": date(2027, 12, 31), "expiry_source": "user"},
     "2027-12-31", None, "a person's expiry overruled by the text"),
    (_facts(), {"effective": date(2026, 10, 15), "expiry": date(2028, 6, 30), "expiry_source": "change_request"},
     "2028-06-30", None, "a finished renewal's new end date recounted from the old term"),
    (_facts(), {"conflict": {"form": "2026-11-17", "text": "2026-10-15"}},
     None, "effective date differs between the form and the contract text - needs a person",
     "counting from a disputed effective date"),
    (_facts(), {}, None, "term counted from the effective date, which is not set - needs a person",
     "a term counted from a date nobody has"),
    (_facts(initial_term=None), {}, None, "term not stated in the contract - needs a person",
     "an unclear term guessed"),
])
def test_the_expiry_comes_only_from_a_person_the_text_or_the_term(facts, anchors, expiry, reason, guards):
    """Guards the slip named in each row's `guards` column."""
    block = renewal_dates_for(facts, **anchors)
    assert (block["expiration_date"], block["reason"]) == (expiry, reason), guards
    if expiry is None:
        assert block["notice_date"] is None and block["window_opens"] is None, guards


@pytest.mark.parametrize("over, reason, guards", [
    ({"notice_terms": [{"kind": "non_renewal", "number": None, "unit": None, "quote": "as required by law"}]},
     "renews automatically but no non-renewal notice period is stated - needs a person",
     "a statutory notice period turned into a date"),
    ({"auto_renews": None}, "the contract does not say whether it renews automatically - needs a person",
     "an unknown renewal treated as 'does not renew'"),
])
def test_a_notice_deadline_the_text_does_not_state_gets_no_date(over, reason, guards):
    """Guards the slip named in each row's `guards` column: the expiry stands,
    the notice deadline waits for a person."""
    block = renewal_dates_for(_facts(**over), effective=date(2026, 10, 15))
    assert (block["expiration_date"], block["notice_date"], block["reason"]) == ("2027-10-15", None, reason), guards


def test_clear_days_take_the_notice_deadline_one_day_earlier():
    """Guards '90 clear days' being read as plain days, a day too late."""
    notice = [{"kind": "non_renewal", "number": 90, "unit": "days", "clear_days": True,
               "quote": "ninety (90) clear days"}]
    block = renewal_dates_for(_facts(notice_terms=notice), effective=date(2026, 10, 15))
    assert block["notice_date"] == "2027-07-16"
    assert block["formulas"]["notice_date"].endswith("- 90 clear days")


# --- writing the event --------------------------------------------------------


class FakeDB:
    def __init__(self, contract, event=None):
        self.contract, self.event, self.commits = contract, event, 0

    def scalar(self, stmt):
        return self.event if "renewal_event" in str(stmt) else None

    def get(self, model, key):
        if model is Contract:
            return self.contract
        return self.event if model is RenewalEvent else None

    def add(self, obj):
        self.event = obj

    def flush(self):
        pass

    def commit(self):
        self.commits += 1

    def refresh(self, obj, **kw):
        pass


def _contract(effective=None, expiry=None, sources=None):
    return SimpleNamespace(id="c-1", org_id="org-1", title="Hooli SaaS", owner_user_id=None,
                           updated_by_user_id="u-9", effective_date=effective, expiration_date=expiry,
                           metadata_json={"field_sources": sources or {}})


@pytest.fixture
def calls(monkeypatch):
    seen = {"audit": [], "timeline": [], "obligations": 0}
    monkeypatch.setattr(renewals, "write_audit_log", lambda db, **kw: seen["audit"].append(kw["action"]))
    monkeypatch.setattr(renewals, "write_timeline_event", lambda db, **kw: seen["timeline"].append(kw["event_type"]))

    def _obligations(db, contract):
        seen["obligations"] += 1

    monkeypatch.setattr("app.obligations.service.recompute_due_dates", _obligations)
    return seen


def _extract(db, contract, output=HOOLI):
    return renewals.apply_extracted_renewal(db, contract=contract, version=None, output=output,
                                           skill_run=SimpleNamespace(id="run-1"),
                                           created_by_user_id=None, request_id=None)


def test_extraction_fills_the_columns_with_a_window_before_the_deadline(calls):
    """Guards the old window that WAS the notice deadline (an alert on the last
    day to act), and an empty contract expiry the close sweep can't see."""
    contract = _contract(effective=date(2026, 10, 15), sources={"effective_date": "user"})
    event = _extract(FakeDB(contract), contract)
    assert (event.expiration_date, event.notice_date, event.renewal_window_starts_at) == (
        date(2027, 10, 15), date(2027, 7, 17), date(2027, 6, 17))
    assert contract.expiration_date == date(2027, 10, 15)
    assert contract.metadata_json["field_sources"]["expiration_date"] == "computed"
    assert calls["obligations"] == 1  # an obligation counted from the expiry can now get its date
    assert renewals.dates_status(event) == "calculated"


def test_extraction_replaces_the_metadata_models_own_expiry_with_the_counted_one(calls):
    """Guards the metadata model's own arithmetic (one day short here) standing as
    the contract's expiry when code has counted the term from its wording."""
    contract = _contract(effective=date(2026, 10, 15), expiry=date(2027, 10, 14),
                         sources={"expiration_date": "ai"})
    _extract(FakeDB(contract), contract)
    assert contract.expiration_date == date(2027, 10, 15)
    assert contract.metadata_json["field_sources"]["expiration_date"] == "computed"
    assert calls["obligations"] == 1


@pytest.mark.parametrize("source", ["user", "form", "change_request"])
def test_code_never_replaces_a_decided_contract_expiry(calls, source):
    """Guards code's counted expiry overwriting a date a person, the request form
    or a finished change request put on the contract."""
    contract = _contract(expiry=date(2027, 12, 31), sources={"expiration_date": source})
    renewals._fill_contract_expiry(FakeDB(contract), contract, {"expiration_date": "2027-10-15"})
    assert contract.expiration_date == date(2027, 12, 31)
    assert contract.metadata_json["field_sources"]["expiration_date"] == source
    assert calls["obligations"] == 0


def test_a_disputed_anchor_clears_the_expiry_code_filled_on_the_contract(calls):
    """Guards the close sweep and expiry-counted obligations still running off an
    expiry code counted from an effective date that is now disputed."""
    contract = _contract(effective=date(2026, 10, 15))
    db = FakeDB(contract)
    _extract(db, contract)
    assert contract.expiration_date == date(2027, 10, 15) and calls["obligations"] == 1
    contract.metadata_json = {
        "field_sources": {**contract.metadata_json["field_sources"], "effective_date": "form"},
        "ai_suggestions": {"metadata": {"effective_date": "2026-11-17"}},
    }
    renewals.recompute_renewal_dates(db, contract)
    assert contract.expiration_date is None
    assert "expiration_date" not in contract.metadata_json["field_sources"]
    assert calls["obligations"] == 2


def test_re_extraction_keeps_the_window_sweeps_notified_marker(calls):
    """Guards a re-extraction wiping notified_at, so the daily sweep emails the
    same renewal window again."""
    contract = _contract(effective=date(2026, 10, 15))
    db = FakeDB(contract)
    event = _extract(db, contract)
    event.metadata_json = {**event.metadata_json, "notified_at": "2027-06-17"}
    _extract(db, contract)
    assert event.metadata_json["notified_at"] == "2027-06-17"
    assert event.metadata_json["dates"]["expiration_date"] == "2027-10-15"


def test_an_anchor_change_recomputes_unconfirmed_dates_and_our_contract_expiry(calls):
    """Guards a person fixing the effective date while the renewal (and the
    contract expiry code filled from it) keep counting from the old one."""
    contract = _contract(effective=date(2026, 10, 15))
    db = FakeDB(contract)
    event = _extract(db, contract)
    contract.effective_date = date(2026, 11, 30)
    renewals.recompute_renewal_dates(db, contract)
    assert (event.expiration_date, event.notice_date) == (date(2027, 11, 30), date(2027, 9, 1))
    assert contract.expiration_date == date(2027, 11, 30)
    assert "renewal.dates_recomputed" in calls["audit"]


def test_recompute_keeps_confirmed_dates_and_shows_the_new_ones_beside_them(calls):
    """Guards an anchor change silently moving a date a person confirmed: it stays,
    is marked needs re-confirming, and the recalculated dates are shown with it."""
    contract = _contract(effective=date(2026, 10, 15))
    db = FakeDB(contract)
    event = _extract(db, contract)
    user = SimpleNamespace(id="u-1", org_id="org-1")
    renewals.RenewalsService(db).confirm_dates(row=event, expiration_date=None, notice_date=None,
                                               current_user=user, request_id=None)
    contract.effective_date = date(2026, 11, 30)
    renewals.recompute_renewal_dates(db, contract)
    dates = event.metadata_json["dates"]
    assert event.expiration_date == date(2027, 10, 15)
    assert (dates["expiration_date"], dates["needs_reconfirm"]) == ("2027-10-15", True)
    assert dates["recomputed"]["expiration_date"] == "2027-11-30"
    assert renewals.dates_status(event) == "needs_reconfirm"
    # Confirming again accepts the recalculated dates.
    renewals.RenewalsService(db).confirm_dates(row=event, expiration_date=None, notice_date=None,
                                               current_user=user, request_id=None)
    assert (event.expiration_date, renewals.dates_status(event)) == (date(2027, 11, 30), "confirmed")


def test_re_extracting_the_same_wording_after_a_confirmed_override_needs_no_reconfirm(calls):
    """Guards a person's own typed notice date being mistaken for an anchor change
    on every re-run, so the row nags for re-confirmation forever."""
    contract = _contract(effective=date(2026, 10, 15))
    db = FakeDB(contract)
    event = _extract(db, contract)
    renewals.RenewalsService(db).confirm_dates(
        row=event, expiration_date=None, notice_date=date(2027, 7, 1),
        current_user=SimpleNamespace(id="u-1", org_id="org-1"), request_id=None)
    assert (event.notice_date, event.renewal_window_starts_at) == (date(2027, 7, 1), date(2027, 6, 1))
    _extract(db, contract)
    dates = event.metadata_json["dates"]
    assert (event.notice_date, dates["needs_reconfirm"], dates["formulas"]["notice_date"]) == (
        date(2027, 7, 1), False, "notice date set by a person")


def test_confirming_a_typed_expiry_recounts_the_notice_and_sets_the_contract(calls):
    """Guards a person's corrected expiry leaving the old notice deadline in place,
    and the contract keeping a different expiry than the renewal."""
    contract = _contract(effective=date(2026, 10, 15))
    db = FakeDB(contract)
    event = _extract(db, contract)
    renewals.RenewalsService(db).confirm_dates(
        row=event, expiration_date=date(2027, 11, 30), notice_date=None,
        current_user=SimpleNamespace(id="u-1", org_id="org-1"), request_id=None)
    assert (event.expiration_date, event.notice_date) == (date(2027, 11, 30), date(2027, 9, 1))
    assert contract.expiration_date == date(2027, 11, 30)
    assert contract.metadata_json["field_sources"]["expiration_date"] == "user"
    assert "renewal.dates_confirmed" in calls["audit"] and db.commits == 1


def test_a_typed_expiry_does_not_nag_for_reconfirmation_on_the_next_recount(calls):
    """Guards a person's typed expiry being compared, on every later recount or
    re-extraction, against the figures code had before they typed it, so the row
    reads "needs re-confirming" though nothing changed."""
    contract = _contract(effective=date(2026, 10, 15))
    db = FakeDB(contract)
    event = _extract(db, contract)
    renewals.RenewalsService(db).confirm_dates(
        row=event, expiration_date=date(2027, 11, 30), notice_date=None,
        current_user=SimpleNamespace(id="u-1", org_id="org-1"), request_id=None)
    renewals.recompute_renewal_dates(db, contract)
    _extract(db, contract)
    assert renewals.dates_status(event) == "confirmed"
    assert (event.expiration_date, event.notice_date) == (date(2027, 11, 30), date(2027, 9, 1))


def test_confirming_with_no_expiry_to_confirm_is_refused(calls):
    """Guards a 'confirmed' badge on a renewal whose expiry is still disputed."""
    contract = _contract(effective=date(2026, 10, 15), expiry=date(2027, 9, 30),
                         sources={"expiration_date": "form"})
    db = FakeDB(contract)
    event = _extract(db, contract)
    assert renewals.dates_status(event) == "needs_person"
    with pytest.raises(HTTPException) as err:
        renewals.RenewalsService(db).confirm_dates(row=event, expiration_date=None, notice_date=None,
                                                   current_user=SimpleNamespace(id="u-1", org_id="org-1"),
                                                   request_id=None)
    assert err.value.status_code == 422


def test_recompute_leaves_an_event_from_before_typed_facts_alone(calls):
    """Guards recomputing an old event whose dates the AI wrote, with no facts to
    count from, into blanks."""
    contract = _contract(effective=date(2026, 10, 15))
    event = RenewalEvent(org_id="org-1", contract_id="c-1", expiration_date=date(2027, 1, 1), metadata_json={})
    renewals.recompute_renewal_dates(FakeDB(contract, event), contract)
    assert event.expiration_date == date(2027, 1, 1) and calls["audit"] == []


# --- what the page, the email and the recommendation see ----------------------


def test_the_response_and_the_window_email_carry_the_formula_and_the_unconfirmed_label(calls):
    """Guards a computed date shown (or emailed) as if a person had checked it."""
    contract = _contract(effective=date(2026, 10, 15))
    contract.lifecycle_stage, contract.renewal_due = "active", False
    event = _extract(FakeDB(contract), contract)
    event.id, event.owner_user_id, event.renewal_window_starts_at = "e-1", "u-1", date(2020, 1, 1)
    body = renewals.serialize_renewal(event)
    assert (body["dates_status"], body["next_expiration_date"]) == ("calculated", "2028-10-15")
    assert body["formulas"]["notice_date"].startswith("notice by Sat 17 Jul 2027")

    sent = []

    class Mail:
        async def send_email(self, **kw):
            sent.append(kw["html"])

    class DB(FakeDB):
        def scalars(self, stmt):
            return SimpleNamespace(all=lambda: [event])

        def get(self, model, key):
            return SimpleNamespace(email="o@example.com") if model.__name__ == "User" else contract

    contract.lifecycle_stage = renewals.ContractLifecycleStage.ACTIVE
    asyncio.run(renewals.RenewalsService(DB(contract, event), resend=Mail()).run_window_check(
        current_user=SimpleNamespace(id="u-1", org_id="org-1"), request_id=None))
    assert sent and "(calculated - not yet confirmed)" in sent[0]


def test_the_recommendation_is_told_whether_the_contract_renews_and_that_dates_are_unconfirmed(calls):
    """Guards the model recommending 'renew' blind to whether the contract renews at all."""
    contract = _contract(effective=date(2026, 10, 15))
    contract.contract_type = contract.counterparty_name = contract.value_amount = None
    contract.risk_summary = None
    event = _extract(FakeDB(contract), contract)
    lines = recommendation._facts(event, contract)
    assert "Renews automatically for 1 year unless notice is given" in lines
    assert any("not yet confirmed" in line for line in lines)


def test_the_portfolio_filter_keeps_dateless_events_that_carry_a_reason():
    """Guards the evergreen MSA vanishing from the renewals page: the JSON path
    used to keep it must compile for Postgres."""
    clause = RenewalEvent.metadata_json["dates"]["reason"].as_string().is_not(None)
    sql = str(select(RenewalEvent.id).where(clause).compile(dialect=postgresql.dialect()))
    assert "->" in sql


# --- a finished change request ------------------------------------------------


def test_a_finished_renewal_request_writes_a_decided_expiry_and_recounts_deadlines(monkeypatch):
    """Guards a renewal workflow's new end date landing on the contract as a plain
    form value (which code's counting could later dispute) while the notice
    deadline and obligations keep counting from the old end date."""
    from app.intake import drafting

    contract = _contract(expiry=date(2027, 10, 15), sources={"expiration_date": "computed"})
    recounted = []
    monkeypatch.setattr(drafting, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(drafting, "write_timeline_event", lambda db, **kw: None)
    monkeypatch.setattr("app.core.deadlines.recompute_contract_deadlines",
                        lambda db, c: recounted.append(c.expiration_date))
    request = SimpleNamespace(org_id="org-1", ref="REQ-1", type_label="Renewal",
                              field_values={"request_form": "renewal", "parent_contract_id": "c-1",
                                            "renew_end": "2028-10-14"})
    drafting.DraftingService(FakeDB(contract), files=object()).apply_change_request(request=request, actor_id="u-1")
    assert contract.expiration_date == date(2028, 10, 14)
    assert contract.metadata_json["field_sources"]["expiration_date"] == "change_request"
    assert recounted == [date(2028, 10, 14)]
