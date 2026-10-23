"""Obligation dates are counted by code from the contract's wording (Step 3d):
the AI copies the period and its trigger, code computes the date with a formula,
a person enters event dates and confirms. The DB tests run inside one
rolled-back transaction."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai.schemas import ObligationTiming
from app.auth.models import User
from app.contracts.models import Contract
from app.core.audit import write_audit_log
from app.core.database import engine
from app.core.deadlines import Anchor
from app.obligations import routes
from app.obligations.models import Obligation, ObligationReminder
from app.obligations.service import (
    RETIRED_REASON,
    ObligationsService,
    apply_extracted_obligations,
    obligation_deadline,
    recompute_due_dates,
    reminder_email,
    retire_ai_computed_obligation_dates,
    set_reminder,
)
from app.organizations.models import Organization

ANCHORS = {
    "effective_date": Anchor("effective_date", date(2026, 10, 15), "user"),
    "expiration_date": Anchor("expiration_date", date(2027, 10, 14), "user"),
    "signature_date": Anchor("signature_date", None, None),
}


def _timing(**kw) -> ObligationTiming:
    return ObligationTiming(**{"kind": "deadline", "stated_in_text": True, **kw})


@pytest.mark.parametrize("timing, quote, event, expected", [
    (_timing(period_number=12, period_unit="months", anchor="effective_date"),
     "within twelve (12) months of the Effective Date", None,
     ("2027-10-15", None, "due Fri 15 Oct 2027 = effective date Thu 15 Oct 2026 + 12 months", None)),
    (_timing(period_number=45, period_unit="days", anchor="effective_date"),
     "no later than forty-five days after the Effective Date", None,
     ("2026-11-29", None, "due Sun 29 Nov 2026 = effective date Thu 15 Oct 2026 + 45 days", None)),
    (_timing(period_number=1, period_unit="months", anchor="effective_date"),
     "within a calendar month of the Effective Date", None,
     ("2026-11-15", None, "due Sun 15 Nov 2026 = effective date Thu 15 Oct 2026 + 1 month", None)),
    (_timing(period_number=60, period_unit="days", anchor="effective_date"),
     "pay all undisputed invoices promptly", None,
     (None, None, None, "period not found in the quoted clause - needs a person")),
    (_timing(kind="ongoing", period_number=2, period_unit="years", anchor="expiration_date"),
     "for two (2) years after expiry", None,
     (None, "2029-10-14", "ends Sun 14 Oct 2029 = expiry Thu 14 Oct 2027 + 2 years", None)),
    (_timing(period_number=30, period_unit="days", anchor="invoice_received", anchor_text="receipt of invoice"),
     "within 30 days of receipt of invoice", date(2026, 11, 2),
     ("2026-12-02", None, "due Wed 2 Dec 2026 = invoice received Mon 2 Nov 2026 + 30 days", None)),
    (_timing(period_number=30, period_unit="days", anchor="invoice_received", anchor_text="receipt of invoice"),
     "within 30 days of receipt of invoice", None,
     (None, None, None, "starts when receipt of invoice - enter the date")),
    (ObligationTiming(kind="ongoing"), "keep the information confidential", None, (None, None, None, None)),
    (ObligationTiming(kind="condition", period_number=5, period_unit="days", stated_in_text=True,
                      anchor="effective_date"), "if audited, within 5 days", None, (None, None, None, None)),
])
def test_obligation_deadline_compute_paths(timing, quote, event, expected):
    """Guards each way a date could be invented: a period the quote doesn't state
    (brought in from law), an ongoing duty given a due date (it would turn
    overdue), an event date nobody entered, or a condition counted as a deadline."""
    block = obligation_deadline(timing, ANCHORS, quote=quote, event_date=event)
    assert (block["due"], block["ends_on"], block["formula"], block["reason"]) == expected


def test_a_disputed_anchor_keeps_both_values_for_the_person():
    """Guards counting from the form's or the text's date when they disagree:
    no date, and the page gets both values to choose between."""
    anchors = {**ANCHORS, "effective_date": Anchor("effective_date", None, "form",
                                                   {"form": "2026-11-17", "text": "2026-10-15"})}
    block = obligation_deadline(_timing(period_number=12, period_unit="months", anchor="effective_date"),
                                anchors, quote="12 months")
    assert block["due"] is None
    assert block["anchor"]["conflict"] == {"form": "2026-11-17", "text": "2026-10-15"}
    assert "needs a person" in block["reason"]


@pytest.mark.parametrize("quote, expected", [
    ("shall deliver the report by 31 March 2027", ("2027-03-31", "due Wed 31 Mar 2027 = stated in the contract")),
    ("within three (3) months of the Effective Date",
     ("2027-01-15", "due Fri 15 Jan 2027 = effective date Thu 15 Oct 2026 + 3 months")),
])
def test_an_explicit_date_counts_only_when_the_quote_writes_it(quote, expected):
    """Guards the model's own arithmetic coming back as an "explicit" date: one
    not in the quote is dropped and the clause's period is counted instead."""
    timing = _timing(period_number=3, period_unit="months", anchor="effective_date",
                     explicit_date=date(2027, 3, 31))
    block = obligation_deadline(timing, ANCHORS, quote=quote)
    assert (block["due"], block["formula"]) == expected


def test_a_dropped_explicit_date_with_no_period_needs_a_person():
    """Guards a date that isn't in the quote still being diarised when there is
    no period to fall back on."""
    timing = ObligationTiming(kind="deadline", explicit_date=date(2027, 3, 31))
    block = obligation_deadline(timing, ANCHORS, quote="deliver the report promptly")
    assert (block["due"], block["reason"]) == (None, "period not stated in the contract - needs a person")


def test_the_reminder_says_when_a_date_is_only_calculated():
    """Guards an unconfirmed computed date reading like a settled one in the email."""
    ob = Obligation(obligation_type="payment", due_date=date(2026, 12, 2),
                    metadata_json={"deadline": {"due": "2026-12-02"}})
    assert "(calculated - not yet confirmed)" in reminder_email(ob)[1]
    ob.metadata_json = {"deadline": {"due": "2026-12-02", "confirmed_by": "u-1"}}
    assert "calculated" not in reminder_email(ob)[1]


# --- against the database -----------------------------------------------------

@pytest.fixture
def db():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def world(db):
    org = Organization(name="Deadlines org", slug=f"dl-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    user = User(org_id=org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="Dee Line",
                hashed_password="hash", status="active")
    db.add(user)
    db.flush()
    contract = Contract(org_id=org.id, title="MSA", owner_user_id=user.id, contract_type="MSA",
                        effective_date=date(2026, 10, 15),
                        metadata_json={"field_sources": {"effective_date": "user"}})
    db.add(contract)
    db.flush()
    return SimpleNamespace(db=db, user=user, contract=contract)


REPORT = {"description": "Deliver the implementation report", "obligation_type": "reporting",
          "timing": {"kind": "deadline", "period_number": 3, "period_unit": "months",
                     "anchor": "effective_date", "stated_in_text": True},
          "citations": [{"quote": "within three (3) months of the Effective Date"}]}
INVOICE = {"description": "Pay each invoice", "obligation_type": "payment",
           "timing": {"kind": "deadline", "period_number": 30, "period_unit": "days",
                      "anchor": "invoice_received", "anchor_text": "receipt of a valid invoice",
                      "stated_in_text": True},
           "citations": [{"quote": "within 30 days of receipt of a valid invoice"}]}


def _extract(w, *items) -> list[Obligation]:
    apply_extracted_obligations(w.db, contract=w.contract, version=None, output={"obligations": list(items)},
                                skill_run=SimpleNamespace(id="run-1"), created_by_user_id=w.user.id,
                                request_id=None)
    w.db.flush()
    return _live(w)


def _live(w) -> list[Obligation]:
    return w.db.scalars(select(Obligation).where(
        Obligation.contract_id == w.contract.id, Obligation.deleted_at.is_(None)
    ).order_by(Obligation.description)).all()


def _reminders(w, ob) -> list[date]:
    return [r.remind_at for r in w.db.scalars(select(ObligationReminder).where(
        ObligationReminder.obligation_id == ob.id, ObligationReminder.sent_at.is_(None)))]


def _patch(w, ob, **fields) -> dict:
    return ObligationsService(w.db).update_obligation(
        ob=ob, payload=routes.ObligationUpdate(**fields), current_user=w.user)


def test_extraction_counts_the_date_and_reminds(world):
    """Guards the model's own date being stored, and computed dates getting no
    reminder: the date comes from the contract's effective date, with a formula."""
    [report, _invoice] = _extract(world, REPORT, INVOICE)
    assert report.due_date == date(2027, 1, 15)
    assert report.metadata_json["deadline"]["formula"].startswith("due Fri 15 Jan 2027 = effective date")
    assert _reminders(world, report) == [date(2027, 1, 8)]


def test_an_entered_event_date_starts_the_count(world):
    """Guards event-driven duties (52 of 53 rows) staying dateless forever: a
    person enters when the invoice arrived and code counts from it."""
    [_report, invoice] = _extract(world, REPORT, INVOICE)
    assert invoice.due_date is None
    body = _patch(world, invoice, anchor_date=date(2026, 11, 2))
    assert invoice.due_date == date(2026, 12, 2)
    assert body["deadline"]["formula"] == "due Wed 2 Dec 2026 = invoice received Mon 2 Nov 2026 + 30 days"
    assert body["deadline"]["confirmed"] is False
    assert _reminders(world, invoice) == [date(2026, 11, 25)]


def test_a_date_typed_by_a_person_gets_a_reminder_and_counts_as_confirmed(world):
    """Guards a manually set due date never reminding anyone (only extraction
    used to write reminders) and being labelled 'calculated'."""
    [_report, invoice] = _extract(world, REPORT, INVOICE)
    body = _patch(world, invoice, due_date=date(2027, 3, 31))
    assert _reminders(world, invoice) == [date(2027, 3, 24)]
    assert (body["deadline"]["confirmed"], body["deadline"]["set_by_person"]) == (True, True)


def test_a_confirmed_obligation_survives_re_extraction(world):
    """Guards a re-run (signed copy, manual extract) soft-deleting every AI row,
    wiping the person's confirmation, and adding the same clause twice."""
    [report, invoice] = _extract(world, REPORT, INVOICE)
    _patch(world, report, confirm=True)
    _extract(world, REPORT, INVOICE)
    live = _live(world)
    assert report in live and invoice not in live
    assert len(live) == 2
    assert report.metadata_json["deadline"]["confirmed_by"] == world.user.id


def test_an_anchor_change_recounts_unconfirmed_dates_and_flags_confirmed_ones(world):
    """Guards a moved effective date leaving stale computed dates, or silently
    overwriting one a person confirmed (it must ask for re-confirming instead)."""
    second = {**REPORT, "description": "Deliver the training plan",
              "citations": [{"quote": "within 3 months of the Effective Date, a training plan"}]}
    [report, plan] = _extract(world, REPORT, second)
    _patch(world, plan, confirm=True)
    world.contract.effective_date = date(2026, 12, 1)
    recompute_due_dates(world.db, world.contract)
    assert report.due_date == date(2027, 3, 1)
    assert _reminders(world, report) == [date(2027, 2, 22)]
    assert plan.due_date == date(2027, 1, 15)
    block = plan.metadata_json["deadline"]
    assert (block["needs_reconfirm"], block["recomputed"]["due"]) == (True, "2027-03-01")
    body = _patch(world, plan, confirm=True)  # confirming accepts the re-count
    assert plan.due_date == date(2027, 3, 1)
    assert body["deadline"]["needs_reconfirm"] is False


def test_a_recount_back_to_the_confirmed_date_clears_the_reconfirm_flag(world):
    """Guards a stale "needs re-confirming" (and a stale re-count beside it) when
    the anchor moves and then moves back to the date the person confirmed."""
    [report, _invoice] = _extract(world, REPORT, INVOICE)
    _patch(world, report, confirm=True)
    world.contract.effective_date = date(2026, 12, 1)
    recompute_due_dates(world.db, world.contract)
    assert report.metadata_json["deadline"]["needs_reconfirm"] is True
    world.contract.effective_date = date(2026, 10, 15)
    recompute_due_dates(world.db, world.contract)
    block = report.metadata_json["deadline"]
    assert (block["needs_reconfirm"], block["recomputed"], block["due"]) == (False, None, "2027-01-15")


def test_confirming_an_obligation_with_no_date_is_refused(world):
    """Guards a "confirmed" badge on a duty with no date at all (an event nobody
    logged): there is nothing to confirm, so the person must set the date."""
    [_report, invoice] = _extract(world, REPORT, INVOICE)
    with pytest.raises(HTTPException) as err:
        _patch(world, invoice, confirm=True)
    assert err.value.status_code == 409


def test_a_kept_row_without_a_quote_does_not_swallow_new_unquoted_items(world):
    """Guards the empty quote of a person-edited row matching every new item that
    has no quote either, so re-extraction silently dropped them."""
    unquoted = {"description": "Keep audit records", "timing": {"kind": "condition"}}
    [kept] = _extract(world, unquoted)
    _patch(world, kept, responsible_party="Supplier")
    _extract(world, {**unquoted, "description": "Maintain insurance"})
    assert [ob.description for ob in _live(world)] == ["Keep audit records", "Maintain insurance"]


def test_re_extraction_drops_the_reminders_of_the_rows_it_replaces(world):
    """Guards an email going out for a soft-deleted obligation: its unsent
    reminder must go with it."""
    [old, _invoice] = _extract(world, REPORT, INVOICE)
    assert _reminders(world, old)
    [new, _invoice2] = _extract(world, REPORT, INVOICE)
    assert old.deleted_at is not None and _reminders(world, old) == []
    assert _reminders(world, new) == [date(2027, 1, 8)]


def test_retiring_pre_step_3d_ai_dates_clears_them_and_their_reminders(world):
    """Guards dates the model computed before Step 3d still reminding people as if
    counted: they are cleared with a reason, but a date a person typed then (only
    the audit log knows) and a date code counted are kept."""
    def legacy(description, **meta):
        ob = Obligation(org_id=world.contract.org_id, contract_id=world.contract.id, description=description,
                        due_date=date(2027, 2, 1), status="open",
                        metadata_json={"source": "ai_extraction", **meta})
        world.db.add(ob)
        world.db.flush()
        set_reminder(world.db, ob)
        return ob

    ai_dated = legacy("AI dated")
    typed = legacy("Typed by a person")
    counted = legacy("Counted by code", deadline={"due": "2027-02-01", "formula": "due ..."})
    write_audit_log(world.db, action="obligation.updated", resource_type="obligation", resource_id=typed.id,
                    org_id=typed.org_id, actor_user_id=world.user.id, after={"due_date": "2027-02-01"})
    assert retire_ai_computed_obligation_dates(world.db, org_id=world.contract.org_id) == 1
    assert (ai_dated.due_date, ai_dated.metadata_json["deadline"]["reason"]) == (None, RETIRED_REASON)
    assert _reminders(world, ai_dated) == []
    assert typed.due_date == counted.due_date == date(2027, 2, 1)
    assert _reminders(world, typed) and _reminders(world, counted)


def test_signature_completion_recounts_deadlines_before_commit(monkeypatch):
    """Guards "within 30 days of signing" staying dateless after the contract is
    signed: completion (the manual sync and the DocuSign webhook both go through
    it) must recount the contract's deadlines in the same transaction."""
    import asyncio

    from app.core import deadlines
    from app.signatures import service as sig

    calls = []
    contract = SimpleNamespace(id="c-1", org_id="org-1")
    version = SimpleNamespace(id="v-1", org_id="org-1")

    class DB:
        def get(self, model, _id):
            return contract if model is sig.Contract else version

        def scalars(self, _stmt):
            return []

        def add(self, _row):
            pass

        def flush(self):
            calls.append("flush")

    request = SimpleNamespace(id="s-1", status="sent", contract_id="c-1", contract_version_id="v-1",
                              provider_envelope_id="env-1")
    service = sig.SignaturesService(DB())

    async def no_download(**_kw):
        return None

    monkeypatch.setattr(service, "_download_and_store_signed_document", no_download)
    monkeypatch.setattr(service, "_create_signed_version", lambda **kw: SimpleNamespace(id="v-2"))
    for name in ("transition_contract_stage", "write_audit_log", "write_timeline_event",
                 "requeue_contract_ai_jobs", "queue_activation_ai_jobs"):
        monkeypatch.setattr(sig, name, lambda *a, **kw: None)
    monkeypatch.setattr(deadlines, "recompute_contract_deadlines",
                        lambda db, c: calls.append(("recompute", c.id, request.completed_at is not None)))
    user = SimpleNamespace(id="u-1", org_id="org-1")
    asyncio.run(service.sync_signature_request(user=user, signature_request=request))
    assert calls == ["flush", ("recompute", "c-1", True)]
