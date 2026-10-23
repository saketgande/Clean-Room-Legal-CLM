"""A notice's response deadline: the AI copies the wording, code counts the date
and shows the sum, a person confirms.

The pure half (compute_due, posture) is pinned table-style. The service half
runs against the dev Postgres inside one transaction that is never committed
(commit is swapped for flush, the ref sequence is stubbed), so nothing is left
behind.
"""

import subprocess
import sys
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.notices.models import Notice
from app.notices.schemas import NoticeCreate, NoticeDeadlineFacts, NoticeUpdate
from app.notices.service import NoticesService, compute_due, deadline_posture

RECEIPT_15 = {
    "stated": True, "explicit_date": None, "period_count": 15, "period_unit": "days",
    "runs_from": "receipt", "runs_from_text": "of receipt hereof",
    "quote": "reply within 15 days of receipt hereof",
}


def _facts(**over) -> dict:
    return {**RECEIPT_15, **over}


# --- compute_due -----------------------------------------------------------------

def test_receipt_period_counts_from_the_received_date_not_the_notice_date():
    """'within 15 days of receipt' used to be resolved against the notice's own
    date, which gives a deadline days earlier (or later) than the law's."""
    out = compute_due(_facts(), notice_date=date(2026, 9, 30), received_at=date(2026, 10, 6))
    assert out["due"] == date(2026, 10, 21)
    assert out["formula"] == "due Wed 21 Oct 2026 = received Tue 6 Oct 2026 + 15 days"
    assert out["needs_person"] is False


def test_notice_date_period_counts_from_the_notice_date():
    """'within 15 days hereof' runs from the notice's date; the received date is
    irrelevant and must not leak in."""
    out = compute_due(_facts(runs_from="notice_date"), notice_date=date(2026, 10, 1), received_at=date(2026, 10, 6))
    assert out["due"] == date(2026, 10, 16)
    assert "notice dated Thu 1 Oct 2026 + 15 days" in out["formula"]


def test_an_explicit_date_is_taken_as_written():
    """A date the notice writes out, with no period, is the deadline as written."""
    out = compute_due(_facts(explicit_date="2026-11-02", period_count=None, period_unit=None),
                      notice_date=None, received_at=None)
    assert out["due"] == date(2026, 11, 2)
    assert out["formula"] == "due Mon 2 Nov 2026 = stated in notice"


def test_a_period_beside_an_explicit_date_is_what_gets_counted():
    """With both, the date used to win — and it is the one the model could have
    worked out itself. Code counts the period and shows the sum instead."""
    out = compute_due(_facts(explicit_date="2026-11-02"), notice_date=None, received_at=date(2026, 10, 6))
    assert out["due"] == date(2026, 10, 21)
    assert "+ 15 days" in out["formula"]


def test_a_period_not_in_the_quote_needs_a_person():
    """A number the model brought in from a statute (the quote says 'within the
    statutory period') must not be counted into a deadline the notice never gave."""
    out = compute_due(_facts(quote="reply within the statutory period"), notice_date=None,
                      received_at=date(2026, 10, 6))
    assert out["due"] is None and out["needs_person"] is True
    assert "not in the quoted sentence" in out["reason"]


def test_clear_days_count_one_day_earlier_and_say_so():
    """'15 clear days' was counted as plain days, a day later than the safe date."""
    out = compute_due(_facts(clear_days=True, quote="reply within 15 clear days of receipt"),
                      notice_date=None, received_at=date(2026, 10, 6))
    assert out["due"] == date(2026, 10, 20)
    assert out["formula"].endswith("+ 15 clear days")


def test_a_weekend_deadline_keeps_its_date():
    """No s.10 roll-forward: a Saturday deadline stays Saturday (the formula shows
    the weekday) because moving it only ever makes the date later."""
    out = compute_due(_facts(period_count=2, quote="reply within 2 days"), notice_date=None, received_at=date(2026, 10, 1))
    assert out["due"] == date(2026, 10, 3)
    assert out["formula"].startswith("due Sat 3 Oct 2026")


def test_business_days_skip_weekends_and_ask_about_holidays():
    """Business days are counted Mon-Fri with no holiday calendar, so the formula
    must tell a person to check holidays."""
    out = compute_due(_facts(period_count=3, period_unit="business_days", quote="within three business days"),
                      notice_date=None, received_at=date(2026, 10, 2))
    assert out["due"] == date(2026, 10, 7)
    assert out["formula"].endswith("(public holidays not counted - please confirm)")


@pytest.mark.parametrize(
    "facts,received,reason_part",
    [
        ({"stated": False}, date(2026, 10, 6), "statutory period may apply"),
        (_facts(runs_from="other", runs_from_text="the date of dispatch"), date(2026, 10, 6), "the date of dispatch"),
        (_facts(runs_from=None, runs_from_text=None), date(2026, 10, 6), "A person must set the date"),
        (_facts(period_count=None), date(2026, 10, 6), "no date or period could be read"),
        (_facts(), None, "received date is not set"),
    ],
)
def test_what_code_cannot_count_gets_no_date_and_needs_a_person(facts, received, reason_part):
    """A statutory period, an anchor code doesn't track, or a missing anchor date
    must produce NO date — a guessed legal deadline is worse than none."""
    out = compute_due(facts, notice_date=date(2026, 10, 1), received_at=received)
    assert out["due"] is None and out["formula"] is None
    assert out["needs_person"] is True
    assert reason_part in out["reason"]


def test_no_reading_at_all_is_not_a_needs_person():
    """The mock/heuristic path returns no deadline reading; that must not flag
    every notice as needing a person."""
    assert compute_due(None, notice_date=None, received_at=None)["needs_person"] is False


# --- posture -----------------------------------------------------------------

def test_needs_date_posture_is_distinct_from_no_deadline():
    """A notice whose deadline couldn't be counted used to read 'No deadline' on
    the register — the most dangerous label it could have."""
    basis = {"needs_person": True, "reason": "statutory"}
    assert deadline_posture(Notice(status="open", deadline_basis=basis))[0] == "needs_date"
    assert deadline_posture(Notice(status="open", deadline_basis=None))[0] == "none"
    assert deadline_posture(Notice(status="closed", deadline_basis=basis))[0] == "none"


def test_patch_payload_tells_a_sent_null_from_an_omitted_field():
    """PATCH could never clear a date because null and 'not sent' looked alike."""
    assert "response_due_date" in NoticeUpdate(response_due_date=None).model_fields_set
    assert "response_due_date" not in NoticeUpdate(subject="x").model_fields_set


# --- service (one uncommitted transaction) -----------------------------------

@pytest.fixture
def svc(db_session, monkeypatch):
    import app.models  # noqa: F401  (every table, so the notice's FKs resolve on flush)
    from app.auth.models import User

    actor = db_session.scalar(select(User).where(User.org_id.is_not(None)).limit(1))
    if actor is None:
        pytest.skip("no user in the dev database")
    service = NoticesService(db_session)
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    monkeypatch.setattr(service, "_next_ref", lambda: "NOT-TEST")
    return service, actor


def _create(service, actor, **over):
    payload = {"subject": "Demand", "counterparty_name": "Northwind", **over}
    return service.create_notice(actor=actor, payload=NoticeCreate(**payload))


def test_create_counts_the_date_and_defaults_the_owner_to_the_filer(svc):
    """A received notice filed with no owner was never chased by the reminder
    sweep; and the saved date must be counted by code, labelled unconfirmed."""
    service, actor = svc
    out = _create(service, actor, received_at=date(2026, 10, 6), deadline_basis=NoticeDeadlineFacts(**RECEIPT_15))
    assert out["owner_user_id"] == actor.id
    assert out["response_due_date"] == date(2026, 10, 21)
    basis = out["deadline_basis"]
    assert basis["set_by"] == "document" and basis["confirmed_by"] is None
    assert basis["formula"] == "due Wed 21 Oct 2026 = received Tue 6 Oct 2026 + 15 days"
    events = [e["body"] for e in out["events"] if e["kind"] == "deadline_changed"]
    assert events and "calculated - not yet confirmed" in events[0]


def test_a_different_date_on_create_is_a_persons_override(svc):
    """The client's date is never trusted as 'the document's': if it differs from
    what code counts, it is recorded as set by a person."""
    service, actor = svc
    out = _create(
        service, actor, received_at=date(2026, 10, 6), response_due_date=date(2026, 10, 19),
        deadline_basis=NoticeDeadlineFacts(**RECEIPT_15),
    )
    assert out["response_due_date"] == date(2026, 10, 19)
    assert out["deadline_basis"]["set_by"] == "person"
    assert out["deadline_basis"]["confirmed_by"] == actor.id


def test_patch_null_clears_the_deadline_and_logs_before_and_after(svc, db_session):
    """A wrong deadline could not be removed (null was skipped) and a date change
    wrote no timeline entry — the trail of who moved a legal deadline was lost."""
    from app.core.models import AuditLog

    service, actor = svc
    created = _create(service, actor, received_at=date(2026, 10, 6), deadline_basis=NoticeDeadlineFacts(**RECEIPT_15))
    out = service.update_notice(
        actor=actor, notice_id=created["id"], payload=NoticeUpdate(response_due_date=None)
    )
    assert out["response_due_date"] is None
    assert out["deadline_posture"] == "none"
    assert out["deadline_basis"]["set_by"] == "person"
    body = [e["body"] for e in out["events"] if e["kind"] == "deadline_changed"][-1]
    assert "received Tue 6 Oct 2026 + 15 days" in body and "no response deadline applies" in body
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.resource_id == created["id"], AuditLog.action == "notice.deadline_changed"
        )
    )
    assert audit.before["response_due_date"] == "2026-10-21"
    assert audit.before["formula"].startswith("due Wed 21 Oct 2026")
    assert audit.after["response_due_date"] is None


def test_moving_the_received_date_keeps_a_confirmed_date_and_offers_the_recount(svc):
    """A confirmed date was overwritten by code's recount the moment a date moved.
    It must stay the deadline, with the recount beside it to re-confirm; confirming
    accepts the recount and clears the flag."""
    service, actor = svc
    created = _create(
        service, actor, received_at=date(2026, 10, 6), deadline_confirmed=True,
        deadline_basis=NoticeDeadlineFacts(**RECEIPT_15),
    )
    assert created["deadline_basis"]["confirmed_by"] == actor.id
    moved = service.update_notice(
        actor=actor, notice_id=created["id"], payload=NoticeUpdate(received_at=date(2026, 10, 8))
    )
    assert moved["response_due_date"] == date(2026, 10, 21)
    basis = moved["deadline_basis"]
    assert basis["needs_reconfirm"] is True and basis["due"] == "2026-10-21"
    assert basis["recomputed"]["due"] == "2026-10-23"
    assert "received Thu 8 Oct 2026 + 15 days" in basis["recomputed"]["formula"]
    confirmed = service.update_notice(
        actor=actor, notice_id=created["id"], payload=NoticeUpdate(confirm_deadline=True)
    )
    assert confirmed["response_due_date"] == date(2026, 10, 23)
    assert confirmed["deadline_basis"]["needs_reconfirm"] is False
    assert confirmed["deadline_basis"]["recomputed"] is None
    assert confirmed["deadline_basis"]["formula"].startswith("due Fri 23 Oct 2026")
    assert any(e["kind"] == "deadline_confirmed" for e in confirmed["events"])


def test_moving_the_date_back_clears_the_reconfirm(svc):
    """A recount that lands back on the confirmed date has nothing to re-confirm."""
    service, actor = svc
    created = _create(
        service, actor, received_at=date(2026, 10, 6), deadline_confirmed=True,
        deadline_basis=NoticeDeadlineFacts(**RECEIPT_15),
    )
    service.update_notice(actor=actor, notice_id=created["id"], payload=NoticeUpdate(received_at=date(2026, 10, 8)))
    back = service.update_notice(actor=actor, notice_id=created["id"], payload=NoticeUpdate(received_at=date(2026, 10, 6)))
    assert back["deadline_basis"]["needs_reconfirm"] is False
    assert back["deadline_basis"]["recomputed"] is None


def test_a_person_set_date_is_never_recounted(svc):
    """Code must never overwrite a date a person set, even when the anchor moves."""
    service, actor = svc
    created = _create(service, actor, received_at=date(2026, 10, 6), deadline_basis=NoticeDeadlineFacts(**RECEIPT_15))
    service.update_notice(actor=actor, notice_id=created["id"], payload=NoticeUpdate(response_due_date=date(2026, 10, 30)))
    out = service.update_notice(actor=actor, notice_id=created["id"], payload=NoticeUpdate(received_at=date(2026, 10, 9)))
    assert out["response_due_date"] == date(2026, 10, 30)


def test_statutory_notice_needs_a_date_and_cannot_be_confirmed_blank(svc):
    """No period in the notice: filed with no date, shown as needs_date, and a
    'confirm' with nothing to confirm is refused rather than recorded."""
    service, actor = svc
    created = _create(service, actor, deadline_basis=NoticeDeadlineFacts(stated=False))
    assert created["response_due_date"] is None
    assert created["deadline_posture"] == "needs_date"
    with pytest.raises(HTTPException) as err:
        service.update_notice(actor=actor, notice_id=created["id"], payload=NoticeUpdate(confirm_deadline=True))
    assert err.value.status_code == 409


def test_reminder_for_an_unconfirmed_date_says_so(svc, db_session):
    """Reminders go out for a calculated date nobody confirmed, but must say it is
    calculated and show the sum, so nobody mistakes it for a checked deadline."""
    from app.notifications.models import Notification

    service, actor = svc
    received = date.today() - timedelta(days=10)
    created = _create(service, actor, received_at=received, deadline_basis=NoticeDeadlineFacts(**RECEIPT_15))
    service.run_reminders(org_id=actor.org_id)
    note = db_session.scalar(
        select(Notification).where(
            Notification.user_id == actor.id, Notification.subject.like("%NOT-TEST%")
        ).order_by(Notification.created_at.desc()).limit(1)
    )
    assert note is not None and created["owner_user_id"] == actor.id
    assert "calculated - not yet confirmed" in note.body
    assert "+ 15 days" in note.body


def test_a_recount_to_a_later_date_is_reminded_again(svc, db_session):
    """The reminder stamp survived a date change: after an 'overdue' reminder for a
    wrong date, the corrected later date was never reminded (t7 ranks below overdue)."""
    service, actor = svc
    today = date.today()
    created = _create(service, actor, received_at=today - timedelta(days=20),
                      deadline_basis=NoticeDeadlineFacts(**RECEIPT_15))
    service.run_reminders(org_id=actor.org_id)
    notice = db_session.get(Notice, created["id"])
    assert notice.last_reminder_stage == "overdue"
    moved = service.update_notice(
        actor=actor, notice_id=created["id"], payload=NoticeUpdate(received_at=today - timedelta(days=10))
    )
    assert moved["response_due_date"] == today + timedelta(days=5)
    assert moved["last_reminder_stage"] is None and moved["last_reminder_at"] is None
    service.run_reminders(org_id=actor.org_id)
    assert db_session.get(Notice, created["id"]).last_reminder_stage == "t7"


# --- migration ---------------------------------------------------------------

@pytest.mark.parametrize(
    "command,expected",
    [
        (["upgrade", "0071_ai_call_log_eval_run:0072_notice_deadline_basis"], "ADD COLUMN deadline_basis"),
        (["downgrade", "0072_notice_deadline_basis:0071_ai_call_log_eval_run"], "DROP COLUMN deadline_basis"),
    ],
)
def test_migration_0072_upgrades_and_downgrades(command, expected):
    """Every migration needs a working downgrade; checked as offline SQL so the
    test never touches a real schema."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *command, "--sql"],
        check=True, capture_output=True, text=True,
    )
    assert expected in result.stdout
