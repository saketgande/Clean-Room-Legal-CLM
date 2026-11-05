"""Obligation dates are counted by code from the contract's wording (Step 3d):
the AI copies the period and its trigger, code computes the date with a formula,
a person enters event dates and confirms. The DB tests run inside one
rolled-back transaction."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (register every mapper)
from app.ai.outcomes import PlainError, failure_from_exception
from app.ai.schemas import ObligationTiming
from app.auth.models import User
from app.contracts.models import Contract
from app.core.audit import write_audit_log
from app.core.database import engine
from app.core.deadlines import Anchor
from app.core.enums import ContractLifecycleStage, UserStatus
from app.grants.models import ResourceGrant
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
    send_due_reminders,
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
def world(db, monkeypatch):
    # set_reminder floors a reminder at "today", so the dates asserted below only hold while the real
    # date is before them. Pin the clock to the contract's effective date (they fail from 2026-11-26
    # on the real one).
    monkeypatch.setattr("app.obligations.service.utcnow", lambda: datetime(2026, 10, 15, 9, tzinfo=UTC))
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


def test_an_empty_or_far_smaller_re_extraction_keeps_the_existing_obligations(world):
    """Guards a re-run that returns nothing (or a cut-off list that still validates) soft-deleting every
    untouched row and its reminder: the job must fail with the old rows still live. A first
    extraction with nothing found, and a modest shrink, still go through."""
    assert _extract(world) == []
    plan = {**REPORT, "description": "Deliver the training plan",
            "citations": [{"quote": "within 3 months of the Effective Date, a training plan"}]}
    old = _extract(world, REPORT, INVOICE, plan)
    for fewer in ([], [REPORT]):
        with pytest.raises(PlainError, match="existing ones were kept") as refused:
            _extract(world, *fewer)
        # A plain ValueError would still raise, but the job would read "Something went wrong inside Aegis".
        assert "existing ones were kept" in failure_from_exception(refused.value)["reason"]
        assert _live(world) == old
    assert _reminders(world, old[0]) or _reminders(world, old[2])
    assert len(_extract(world, REPORT, INVOICE)) == 2


def test_a_legal_hold_row_survives_re_extraction(world):
    """Guards a held obligation being soft-deleted by a re-run: legal_hold was a column nothing read."""
    [report, invoice] = _extract(world, REPORT, INVOICE)
    report.legal_hold = True
    _extract(world, REPORT, INVOICE)
    live = _live(world)
    assert report in live and invoice not in live and len(live) == 2


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


# --- contracts that are no longer tracked -----------------------------------------------------------------------


class _Mailbox:
    def __init__(self):
        self.sent: list[str] = []

    async def send_email(self, *, to, subject, html):
        self.sent.append(to)


def _owner(w, label: str) -> User:
    user = User(org_id=w.contract.org_id, email=f"{label}-{uuid.uuid4().hex[:6]}@example.com", full_name=label,
                hashed_password="hash", status="active")
    w.db.add(user)
    w.db.flush()
    return user


def _due(w, contract: Contract, owner: User, due: date, remind_at: date) -> Obligation:
    ob = Obligation(org_id=w.contract.org_id, contract_id=contract.id, owner_user_id=owner.id, description="Pay",
                    obligation_type="payment", due_date=due, status="open")
    w.db.add(ob)
    w.db.flush()
    w.db.add(ObligationReminder(org_id=ob.org_id, obligation_id=ob.id, remind_at=remind_at, channel="email"))
    w.db.flush()
    return ob


UNTRACKED = {
    "deleted": {"deleted_at": datetime(2026, 10, 1, tzinfo=UTC)},
    "closed": {"lifecycle_stage": ContractLifecycleStage.CLOSED},
    "archived": {"archived": True},
}


def _untracked_contract(w, how: str) -> Contract:
    gone = Contract(org_id=w.contract.org_id, title="Untracked MSA", owner_user_id=w.user.id, contract_type="MSA",
                    **UNTRACKED[how])
    w.db.add(gone)
    w.db.flush()
    return gone


@pytest.mark.parametrize("how", UNTRACKED)
def test_the_manual_run_leaves_an_untracked_contracts_obligations_alone(world, how):
    """Guards a deleted, closed or archived contract's duties still being marked overdue and emailed, though
    the Close dialog says it is "no longer tracked": the run read obligations alone and never asked about
    their contract."""
    kept, lost = _owner(world, "kept"), _owner(world, "lost")
    world.contract.owner_user_id = kept.id  # the reminder goes to someone who can open the contract
    live = _due(world, world.contract, kept, date(2026, 10, 10), date(2026, 10, 15))
    gone = _due(world, _untracked_contract(world, how), lost, date(2026, 10, 10), date(2026, 10, 15))
    box = _Mailbox()

    asyncio.run(ObligationsService(world.db, resend=box).run_reminders(current_user=world.user, request_id=None))

    assert kept.email in box.sent and lost.email not in box.sent
    assert (live.status, gone.status) == ("overdue", "open")


@pytest.mark.parametrize("how", UNTRACKED)
def test_the_daily_sweep_leaves_an_untracked_contracts_obligations_alone(world, monkeypatch, how):
    """Guards the same for the nightly job, whose set-based UPDATEs and reminder batch also never joined
    the contract (a batch full of untracked rows would also have used up its limit)."""
    from app.jobs import tasks

    today = tasks.utcnow().date()
    kept, lost = _owner(world, "kept"), _owner(world, "lost")
    world.contract.owner_user_id = kept.id
    live = _due(world, world.contract, kept, today - timedelta(days=3), today - timedelta(days=1))
    gone = _due(world, _untracked_contract(world, how), lost, today - timedelta(days=3), today - timedelta(days=1))
    box = _Mailbox()
    # Read before the run: the sweep closes the session it is given, which detaches these.
    live_id, gone_id, kept_email, lost_email = live.id, gone.id, kept.email, lost.email
    monkeypatch.setattr(tasks, "SessionLocal", lambda: world.db)

    asyncio.run(tasks._send_obligation_reminders(resend=box))

    assert kept_email in box.sent and lost_email not in box.sent
    assert world.db.get(Obligation, live_id).status == "overdue"
    assert world.db.get(Obligation, gone_id).status == "open"


def test_the_obligations_page_lists_tracked_contracts_only_unless_one_is_asked_for(world):
    """Guards a removed or closed contract's duties showing, counting and sitting "overdue" on the tracking
    page for ever; opening that contract by id still lists them, as its history."""
    owner = _owner(world, "owner")
    world.contract.owner_user_id = world.user.id
    live = _due(world, world.contract, owner, date(2026, 12, 1), date(2026, 11, 24))
    deleted = _due(world, _untracked_contract(world, "deleted"), owner, date(2026, 12, 1), date(2026, 11, 24))
    closed_contract = _untracked_contract(world, "closed")
    closed = _due(world, closed_contract, owner, date(2026, 12, 1), date(2026, 11, 24))

    def listed(**kw):
        rows = routes.list_obligations(contract_id=kw.get("contract_id"), status_filter=None,
                                       due_within_days=None, db=world.db, current_user=world.user,
                                       limit=100, offset=0)
        return {r["id"] for r in rows}

    page = listed()
    assert live.id in page and deleted.id not in page and closed.id not in page
    assert listed(contract_id=closed_contract.id) == {closed.id}


@pytest.mark.parametrize("decision, closed", [
    (None, True),  # no renewal on file: an expired contract is closed, as before
    ("renew", False),
    ("renegotiate", False),
])
def test_the_nightly_close_skips_a_contract_whose_renewal_was_decided_to_continue(world, monkeypatch, decision, closed):
    """Guards "Renew" closing the contract: it does not move the end date, so the nightly job closed a
    renewed contract after the old expiry and, now that a closed contract's duties are not tracked, would
    have silenced them too."""
    from app.jobs import tasks
    from app.renewals.models import RenewalEvent

    contract = Contract(org_id=world.contract.org_id, title="Expired MSA", owner_user_id=world.user.id,
                        lifecycle_stage=ContractLifecycleStage.ACTIVE,
                        expiration_date=tasks.utcnow().date() - timedelta(days=3))
    world.db.add(contract)
    world.db.flush()
    if decision:
        world.db.add(RenewalEvent(org_id=contract.org_id, contract_id=contract.id, decision=decision))
        world.db.flush()
    contract_id = contract.id
    monkeypatch.setattr(tasks, "SessionLocal", lambda: world.db)

    tasks.close_expired_contracts()

    stage = world.db.get(Contract, contract_id).lifecycle_stage
    assert (stage == ContractLifecycleStage.CLOSED) is closed


# --- editing a row safely ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    {"status": None},
    {"responsible_party": "x" * 256},
    {"obligation_type": "x" * 161},
    {"recurrence": "x" * 121},
    {"owner_user_id": "x" * 37},
    {"due_date": date(1, 1, 1)},
    {"anchor_date": date(3000, 1, 1)},
])
def test_an_edit_with_a_bad_value_is_refused_before_it_reaches_the_database(bad):
    """Guards 500s with a generic 'Update failed': a null status (NOT NULL), text over a column's width and
    a year-1 date (it overflowed the reminder date) each reached the database or the date maths."""
    with pytest.raises(ValueError):
        routes.ObligationUpdate(**bad)


def test_an_edit_that_leaves_a_field_out_or_in_range_is_accepted():
    """Guards the new limits refusing ordinary edits."""
    routes.ObligationUpdate(responsible_party="x" * 255, recurrence="monthly", due_date=date(2027, 1, 15))
    routes.ObligationUpdate(status="completed")


def test_an_edit_made_from_a_stale_copy_is_refused_instead_of_undoing_the_newer_one(world):
    """Guards the dialog's old copy reverting a colleague's change (a completed row reopened, an owner put
    back) with a 200: the version the editor saw must still be the row's."""
    [report, _invoice] = _extract(world, REPORT, INVOICE)
    seen = report.updated_at.isoformat()
    _patch(world, report, responsible_party="Someone else's edit")

    with pytest.raises(HTTPException) as err:
        _patch(world, report, responsible_party="From the old copy", expected_updated_at=seen)
    assert err.value.status_code == 409
    assert report.responsible_party == "Someone else's edit"

    _patch(world, report, responsible_party="From a fresh copy", expected_updated_at=report.updated_at.isoformat())
    assert report.responsible_party == "From a fresh copy"


def test_an_edit_works_from_the_row_as_it_is_now_not_as_it_was_loaded(world):
    """Guards a concurrent 'confirm' being lost: metadata was rewritten from the copy this request loaded
    before the other save, so the confirmation (the sign-off on a deadline) silently disappeared."""
    [report, _invoice] = _extract(world, REPORT, INVOICE)
    other = Session(bind=world.db.get_bind(), expire_on_commit=False)
    try:
        theirs = other.get(Obligation, report.id)
        ObligationsService(other).update_obligation(
            ob=theirs, payload=routes.ObligationUpdate(confirm=True), current_user=world.user)
        assert (report.metadata_json["deadline"] or {}).get("confirmed_by") is None  # this copy is stale

        _patch(world, report, responsible_party="Acme Ltd")  # no version sent: the lock + re-read is what protects

        deadline = world.db.get(Obligation, report.id).metadata_json["deadline"]
        assert deadline["confirmed_by"] == world.user.id and report.responsible_party == "Acme Ltd"
    finally:
        other.close()


def test_a_row_deleted_by_re_extraction_cannot_be_edited_or_completed(world):
    """Guards a late save or Complete landing on a row the re-extraction just replaced: it returned 200
    on the deleted row and, for a recurring one, spawned a successor from it."""
    [report, _invoice] = _extract(world, REPORT, INVOICE)
    report.deleted_at = datetime(2026, 10, 15, tzinfo=UTC)
    world.db.flush()

    with pytest.raises(HTTPException) as edit:
        _patch(world, report, responsible_party="Late")
    assert edit.value.status_code == 404
    with pytest.raises(HTTPException) as done:
        ObligationsService(world.db).complete_obligation(ob=report, current_user=world.user)
    assert done.value.status_code == 404


def test_a_cancelled_obligation_cannot_be_completed_and_start_a_new_series(world):
    """Guards Complete on a cancelled monthly row spawning a successor, restarting a series someone stopped."""
    monthly = {**REPORT, "description": "Monthly report"}
    [row] = _extract(world, monthly)
    _patch(world, row, status="cancelled", recurrence="monthly")
    before = len(_live(world))

    with pytest.raises(HTTPException) as err:
        ObligationsService(world.db).complete_obligation(ob=row, current_user=world.user)

    assert err.value.status_code == 409 and len(_live(world)) == before


def test_a_closed_obligation_does_not_take_a_new_date_unless_it_is_reopened(world):
    """Guards a late edit rewriting a completed duty's date (and arming a reminder for it)."""
    [report, _invoice] = _extract(world, REPORT, INVOICE)
    _patch(world, report, status="completed")

    with pytest.raises(HTTPException) as err:
        _patch(world, report, due_date=date(2027, 6, 1))
    assert err.value.status_code == 409

    _patch(world, report, status="open", due_date=date(2027, 6, 1))
    assert report.due_date == date(2027, 6, 1)


# --- sending reminders ------------------------------------------------------------------------------------------

TODAY = date(2026, 10, 15)


def _due_reminder(world, owner: User, label: str = "Pay") -> tuple[Obligation, ObligationReminder]:
    """An obligation the clock-pinned world owes today, owned by `owner`, with its reminder due."""
    ob = _due(world, world.contract, owner, TODAY - timedelta(days=1), TODAY)
    ob.description = label
    return ob, world.db.scalars(select(ObligationReminder).where(ObligationReminder.obligation_id == ob.id)).one()


def _send(world, box):
    return asyncio.run(send_due_reminders(world.db, box, today=TODAY, org_id=world.contract.org_id))


def test_a_reminder_is_marked_sent_only_after_its_email_was_accepted(world):
    """Guards a crash, deploy or provider outage losing a deadline reminder for good: it was marked sent and
    committed BEFORE the email went out. Now it stays unsent until the provider accepts it, so the worst a
    crash does is repeat the one in flight."""
    owner = world.user
    _ob, reminder = _due_reminder(world, owner)
    seen = []

    class Box:
        async def send_email(self, *, to, subject, html):
            seen.append(reminder.sent_at)  # what the row says while the email is going out

    result = _send(world, Box())

    assert seen == [None] and reminder.sent_at == TODAY and result["reminders_sent"] == 1


def test_a_failed_send_leaves_the_reminder_for_the_next_run(world):
    """Guards a bounced or crashed send being recorded as delivered."""
    _ob, reminder = _due_reminder(world, world.user)

    class Down:
        async def send_email(self, *, to, subject, html):
            raise RuntimeError("provider down")

    class Crash:
        async def send_email(self, *, to, subject, html):
            raise KeyboardInterrupt  # a worker kill is not an Exception: it must not mark the row either

    assert _send(world, Down())["reminders_failed"] == 1 and reminder.sent_at is None
    with pytest.raises(KeyboardInterrupt):
        _send(world, Crash())
    assert reminder.sent_at is None
    box = _Mailbox()
    assert _send(world, box)["reminders_sent"] == 1 and box.sent == [world.user.email]


def test_a_reminder_goes_to_an_owner_who_can_still_open_the_contract(world):
    """Guards emailing someone who left or was walled off: the owner must be active and able to open the
    contract, else the contract's own owner gets it, else it is skipped (left unsent) and counted."""
    gone, kept = _owner(world, "gone"), world.user
    gone.status = UserStatus.DEACTIVATED
    _ob, reminder = _due_reminder(world, gone)
    box = _Mailbox()

    assert _send(world, box)["reminders_sent"] == 1 and box.sent == [kept.email]

    reminder.sent_at = None  # same reminder again: now nobody valid is left
    kept.status = UserStatus.SUSPENDED
    box2 = _Mailbox()
    result = _send(world, box2)
    assert box2.sent == [] and reminder.sent_at is None
    assert (result["reminders_sent"], result["reminders_skipped"]) == (0, 1)


def test_an_owner_with_no_access_to_the_contract_is_not_emailed(world):
    """Guards the access half of the check: an active user with no right to open the contract (not its
    owner, no grant) is passed over for the contract's owner."""
    stranger = _owner(world, "stranger")
    _ob, _reminder = _due_reminder(world, stranger)
    box = _Mailbox()

    _send(world, box)

    assert box.sent == [world.user.email] and stranger.email not in box.sent


def test_a_reminder_replaced_while_the_batch_runs_is_skipped_not_fatal(world):
    """Guards one date edit during the 08:00 sweep killing it: the edit deletes and re-creates the unsent
    reminder, and writing 'sent' on the deleted row raised StaleDataError, so every later reminder waited
    a day."""
    _ob1, first = _due_reminder(world, world.user, "First")
    _ob2, second = _due_reminder(world, world.user, "Second")
    box = _Mailbox()
    # Both are due today, so the run takes them in id order (random): the one it reaches second is the
    # one a person "edits" while the first is being sent.
    later = sorted([first, second], key=lambda r: (r.remind_at, r.id))[1]

    class EditsTheOther:
        async def send_email(self, *, to, subject, html):
            box.sent.append(to)
            if len(box.sent) == 1:  # a person changes the other obligation's date mid-run
                world.db.delete(later)
                world.db.flush()

    result = _send(world, EditsTheOther())

    assert result["reminders_sent"] == 1 and len(box.sent) == 1


def test_an_owner_must_be_active_and_able_to_open_the_contract(world):
    """Guards reassigning an obligation to someone who would be emailed about a contract they cannot open
    (and see nothing of in the list)."""
    [report, _invoice] = _extract(world, REPORT, INVOICE)
    outsider, leaver, insider = _owner(world, "outsider"), _owner(world, "leaver"), _owner(world, "insider")
    leaver.status = UserStatus.DEACTIVATED
    world.db.add(ResourceGrant(org_id=world.contract.org_id, principal_type="user", principal_id=insider.id,
                               resource_type="contract", resource_id=world.contract.id, access_level="read"))
    world.db.flush()

    for bad in (outsider, leaver):
        with pytest.raises(HTTPException) as err:
            _patch(world, report, owner_user_id=bad.id)
        assert err.value.status_code == 422
    _patch(world, report, owner_user_id=insider.id)
    assert report.owner_user_id == insider.id
