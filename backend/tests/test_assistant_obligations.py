"""Ask Aegis says what is true about obligations (app/obligations/assistant.py and the three tools in
app/ai/tool_runtime.py): an empty list for a contract whose obligations were never read is "unknown", not "none", and
an obligation with no date is not "not due". The wording helpers are pure; the tools run against real rows in one
rolled-back transaction, in an organisation of their own. The model is never called and nothing is dispatched."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

import app.models  # noqa: F401  (register every mapper)
from app.ai import tool_registry as registry
from app.ai import tool_runtime as tr
from app.ai.tool_registry import (
    AttentionItemsInput,
    ExtractObligationsInput,
    ListObligationsInput,
)
from app.assistant.models import AssistantSession, AssistantToolCall
from app.auth.models import User
from app.contracts.models import Contract
from app.core.database import utcnow
from app.core.enums import JobStatus
from app.jobs.service import JobsService
from app.obligations import extraction_audit
from app.obligations.assistant import date_note, obligations_note
from app.obligations.extraction_audit import extraction_state, extraction_states
from app.obligations.service import RETIRED_REASON, ObligationsService
from app.signatures.models import SignatureRequest
from tests.test_extraction_state import (
    _db_job,
    _obligation,
    _with_text,
    new_world,
    rolled_back_session,
)

# --- the wording, pure -------------------------------------------------------------------------------------------


def _ob(due=None, block="absent"):
    meta = {} if block == "absent" else {"deadline": block}
    return SimpleNamespace(due_date=due, metadata_json=meta, status="open")


def test_a_dated_obligation_has_no_date_note():
    assert date_note(_ob(due=utcnow().date())) is None


def test_an_obligation_read_before_dates_were_counted_says_its_timing_is_unknown_not_that_it_is_not_due():
    """Guards the model reading a bare null due_date as "nothing due": 43 of 111 dev obligations are exactly this."""
    note = date_note(_ob())
    assert "unknown" in note and "not the same as not due" in note


def test_a_person_needed_obligation_carries_the_deadline_blocks_own_reason():
    block = {"facts": {"kind": "period"}, "reason": "The clause gives no period."}
    assert date_note(_ob(block=block)) == "The clause gives no period."
    assert date_note(_ob(block={"facts": {}, "reason": "starts when the Effective Date is entered"})) == (
        "starts when the Effective Date is entered"
    )


def test_a_retired_ai_date_is_explained_without_the_internal_sentence():
    """Guards the model repeating "Step 3d" and "re-run extraction" to a person: the stored sentence is for the page's
    Edit button, and a re-run is refused for a contract already read."""
    note = date_note(_ob(block={"facts": None, "reason": RETIRED_REASON}))
    assert "Step 3d" not in note and "re-run" not in note and "A person sets it" in note


def test_an_ongoing_duty_is_described_as_ongoing_not_as_a_gap():
    note = date_note(_ob(block={"facts": {"kind": "ongoing"}, "reason": "Survives three years."}))
    assert "Ongoing" in note and "by its nature" in note


def _summary(**dates):
    counts = {"dated": 0, "waiting_event": 0, "needs_person": 0, "no_fixed_date": 0, "not_read": 0, **dates}
    return {"dates": counts, "needs_date": counts["needs_person"] + counts["not_read"],
            "line": "1 dated, 3 need a date"}


def test_nothing_to_qualify_adds_no_sentence():
    """Guards noise on a clean portfolio: a sentence on every answer teaches the model to ignore it."""
    assert obligations_note(summary=_summary(dated=4), windowed=False, unread=[]) == ""


def test_undated_obligations_get_a_sentence_that_says_how_many_need_a_person():
    note = obligations_note(summary=_summary(dated=1, needs_person=1, not_read=2), windowed=False, unread=[])
    assert "NOT 'not due'" in note and "3 need a date set by a person" in note
    assert "not in it" not in note  # no window: every undated row is in the list


def test_a_window_says_the_undated_ones_are_not_in_the_list():
    """Guards "due in the next 30 days: none" being read as "nothing else is due" when undated ones exist."""
    note = obligations_note(summary=_summary(needs_person=1, no_fixed_date=2), windowed=True, unread=[])
    assert "3 undated ones are not in it" in note and "do not say nothing else is due" in note


def test_unread_contracts_are_named_and_never_called_empty():
    unread = [{"contract_id": "c1", "title": "Globex NDA", "state": "failed", "reason": "cut off"}]
    note = obligations_note(summary=_summary(), windowed=False, unread=unread)
    assert "1 Active contract may have no obligations on file" in note
    assert "Globex NDA (could not be read)" in note and "Never say these have none" in note


def test_many_unread_contracts_name_five_and_count_the_rest():
    unread = [{"contract_id": f"c{i}", "title": f"NDA {i}", "state": "never", "reason": None} for i in range(8)]
    note = obligations_note(summary=_summary(), windowed=False, unread=unread)
    assert "8 Active contracts" in note and "NDA 4 (not read yet)" in note and "NDA 5" not in note
    assert "and 3 more" in note


# --- world helpers -----------------------------------------------------------------------------------------------


@pytest.fixture
def w():
    with rolled_back_session() as db:
        world = new_world(db)
        # new_world's own contract is Active with no text; here every contract is one a test made, so it steps aside.
        world.contract.lifecycle_stage = "intake"
        db.flush()
        yield world


def _contract(w, title, stage="active", **fields):
    """A further contract in the world's organisation; the helpers imported above act on `w.contract`."""
    w.contract = Contract(org_id=w.org.id, title=title, owner_user_id=w.user.id, lifecycle_stage=stage, **fields)
    w.db.add(w.contract)
    w.db.flush()
    return w.contract


def _read(w):
    """The current contract's extraction succeeded (obligations are added by the caller)."""
    version = _with_text(w)
    _db_job(w, version, JobStatus.SUCCEEDED, attempt_count=1)
    return version


def _failed(w):
    version = _with_text(w)
    _db_job(w, version, JobStatus.FAILED, attempt_count=3, finished_at=datetime.now(UTC),
            meta={"failure": {"reason": "The AI's answer was cut off.", "retryable": False}})


NEEDS_PERSON = {"deadline": {"facts": {"kind": "period"}, "reason": "The clause gives no period."}}
WAITING = {"deadline": {"facts": {"kind": "period"}, "reason": "starts when the Effective Date is entered"}}
ONGOING = {"deadline": {"facts": {"kind": "ongoing"}, "reason": "Survives three years."}}
run = tr.tool_runtime


def _list(w, **fields):
    return run._list_obligations(w.db, payload=ListObligationsInput(**fields), user=w.user)


def _attention(w):
    return run._my_attention_items(w.db, payload=AttentionItemsInput(window_days=7), user=w.user)


# --- the loader that reads many contracts at once ----------------------------------------------------------------


def test_the_batch_state_is_the_single_state_for_every_kind_of_contract(w):
    """Guards two answers to "was this contract read?": the page's row (extraction_state, one contract) and the
    chat (extraction_states, many). They share `decide`, and this pins that they feed it the same facts."""
    made = [_contract(w, "no version"), _contract(w, "never")]
    _with_text(w)  # "never": text, but no job
    made.append(_contract(w, "done"))
    _read(w)
    _obligation(w)
    made.append(_contract(w, "none found"))
    _read(w)
    _obligation(w, deleted_at=datetime.now(UTC))  # a deleted row is not an obligation on file
    made.append(_contract(w, "failed"))
    _failed(w)
    made.append(_contract(w, "running"))
    _db_job(w, _with_text(w), JobStatus.QUEUED)
    made.append(_contract(w, "stale"))
    old = _with_text(w, number=1)
    _db_job(w, old, JobStatus.SUCCEEDED, attempt_count=1)  # a reading of the OLD version is not this one's
    _obligation(w, contract_version_id=old.id)
    _with_text(w, number=2)

    batch = extraction_states(w.db, made)

    assert {c.title: batch[c.id].state for c in made} == {
        "no version": "no_text", "never": "never", "done": "done", "none found": "none_found",
        "failed": "failed", "running": "running", "stale": "stale"}
    for c in made:
        one, many = extraction_state(w.db, c), batch[c.id]
        assert (many.state, many.reason, many.attempts, many.retryable, many.live_obligations) == (
            one.state, one.reason, one.attempts, one.retryable, one.live_obligations), c.title
    assert extraction_states(w.db, []) == {}


def test_every_active_contract_is_checked_not_just_the_first_batch(w, monkeypatch):
    """Guards the answer "none are unread" for a portfolio bigger than the cut: the old limit(200) came before the
    filter and dropped the newest contracts, the ones most likely never read. Batches of 2 stand in for 500."""
    monkeypatch.setattr(extraction_audit, "_BATCH", 2)
    for i in range(5):
        _contract(w, f"NDA {i}")
        _read(w) if i in (0, 2) else _with_text(w)  # 1, 3 and 4 were never read, across three batches

    got = extraction_audit.needs_reading(w.db, w.user)

    assert sorted(c.title for c, _ in got) == ["NDA 1", "NDA 3", "NDA 4"]
    assert {s.state for _, s in got} == {"never"}


# --- list_obligations --------------------------------------------------------------------------------------------


def test_each_row_says_whether_it_has_a_date_and_the_result_counts_the_undated_ones(w):
    """Guards the answer "you have 1 obligation due" when 2 more exist that nobody has dated: the rows carry their
    date state and why, the result counts the whole portfolio, and one sentence tells the model how to word it."""
    today = utcnow().date()
    _contract(w, "Services agreement")
    _read(w)
    _obligation(w, description="Pay the invoice", due_date=today + timedelta(days=3))
    _obligation(w, description="Name the period", metadata_json=NEEDS_PERSON)
    _obligation(w, description="Older row")  # read before dates were counted: no deadline block at all

    out = _list(w)

    assert out["obligations"][0]["description"] == "Pay the invoice"  # dated first, the undated ones after it
    rows = {r["description"]: r for r in out["obligations"]}
    assert rows["Pay the invoice"]["date_state"] == "dated" and rows["Pay the invoice"]["date_note"] is None
    assert rows["Name the period"]["date_state"] == "needs_person"
    assert rows["Name the period"]["date_note"] == "The clause gives no period."
    assert rows["Older row"]["date_state"] == "not_read" and "not the same as not due" in rows["Older row"]["date_note"]
    assert (out["count"], out["needs_date"], out["dates"]["dated"], out["unread_contracts"]) == (3, 2, 1, [])
    assert "NOT 'not due'" in out["summary"] and "2 need a date set by a person" in out["summary"]


def test_a_due_window_still_counts_the_undated_ones_it_leaves_out(w):
    """Guards "due in the next 30 days: 1" being read as "nothing else is due": the window drops every undated row,
    so the portfolio counts and the sentence say they exist."""
    today = utcnow().date()
    _contract(w, "Services agreement")
    _read(w)
    _obligation(w, description="Pay the invoice", due_date=today + timedelta(days=3))
    _obligation(w, description="Pay later", due_date=today + timedelta(days=90))
    _obligation(w, description="Name the period", metadata_json=NEEDS_PERSON)

    out = _list(w, due_within_days=30)

    assert [r["description"] for r in out["obligations"]] == ["Pay the invoice"] and out["count"] == 1
    assert out["needs_date"] == 1 and out["dates"]["dated"] == 2
    assert "1 undated ones are not in it" in out["summary"] and "do not say nothing else is due" in out["summary"]


def test_waiting_and_ongoing_obligations_are_not_alarms_but_still_say_they_have_no_date(w):
    """Guards false alarms: a duty that starts at an event, or ends by its nature, is not "needs a date"."""
    _contract(w, "Services agreement")
    _read(w)
    _obligation(w, description="After the event", metadata_json=WAITING)
    _obligation(w, description="Stay quiet", metadata_json=ONGOING)

    out = _list(w)

    assert out["needs_date"] == 0
    assert {r["description"]: r["date_state"] for r in out["obligations"]} == {
        "After the event": "waiting_event", "Stay quiet": "no_fixed_date"}
    assert all(r["date_note"] for r in out["obligations"])


def test_an_active_contract_nobody_read_is_listed_as_not_read_never_as_having_none(w):
    """Guards the real bug: list_obligations returned [] for a contract whose extraction failed or never ran, and the
    assistant answered "no obligations". Failed, never-read and being-read contracts are named with their state; a
    contract that was read, or one not yet Active, is not."""
    _contract(w, "Failed one")
    _failed(w)
    _contract(w, "Never one")
    _with_text(w)
    _contract(w, "Running one")
    _db_job(w, _with_text(w), JobStatus.QUEUED)
    _contract(w, "Read one")
    _read(w)
    _obligation(w, due_date=utcnow().date() + timedelta(days=5))
    _contract(w, "Still in review", stage="review")
    _with_text(w)

    out = _list(w)

    assert {u["title"]: u["state"] for u in out["unread_contracts"]} == {
        "Failed one": "failed", "Never one": "never", "Running one": "running"}
    assert out["count"] == 1
    assert "3 Active contracts may have no obligations on file" in out["summary"]
    assert "Failed one (could not be read)" in out["summary"] and "Running one (being read now)" in out["summary"]
    assert "Still in review" not in out["summary"] and "Read one" not in out["summary"]


def test_a_long_unread_list_is_cut_for_the_model_but_the_full_count_is_told(w, monkeypatch):
    """Guards a result of hundreds of contract entries, and a model reading the cut list as the whole set."""
    from app.obligations import assistant

    monkeypatch.setattr(assistant, "UNREAD_LISTED", 2)
    for i in range(3):
        _contract(w, f"NDA {i}")
        _with_text(w)

    out = _list(w)

    assert len(out["unread_contracts"]) == 2 and out["unread_total"] == 3
    assert "3 Active contracts may have no obligations on file" in out["summary"] and "NDA 2" in out["summary"]


def test_the_list_holds_the_same_obligations_the_page_lists(w):
    """Guards two answers to "how many obligations": the page (visible_obligations) and the chat. A closed contract is
    no longer tracked, so its duties are in neither; every status of a followed contract is in both."""
    today = utcnow().date()
    _contract(w, "Followed")
    _read(w)
    _obligation(w, due_date=today, status="open")
    _obligation(w, due_date=today, status="completed")
    _contract(w, "Closed", stage="closed")
    _read(w)
    _obligation(w, due_date=today - timedelta(days=9), status="overdue")
    _contract(w, "Archived", archived=True)
    _read(w)
    _obligation(w, due_date=today, status="open")

    out = _list(w)

    page = ObligationsService(w.db).summary(user=w.user)
    assert out["count"] == page["total"] == 2
    assert out["dates"] == page["dates"] and out["needs_date"] == page["needs_date"]
    assert {r["contract_title"] for r in out["obligations"]} == {"Followed"}


def test_dated_rows_come_first_soonest_first_and_undated_ones_last(w):
    """Guards undated rows being listed among, or before, the dated ones and burying what is due."""
    today = utcnow().date()
    _contract(w, "Services agreement")
    _read(w)
    _obligation(w, description="undated")
    _obligation(w, description="later", due_date=today + timedelta(days=20))
    _obligation(w, description="sooner", due_date=today + timedelta(days=2))

    assert [r["description"] for r in _list(w)["obligations"]] == ["sooner", "later", "undated"]


def test_grouping_by_counterparty_keeps_the_date_honesty(w):
    _contract(w, "Services agreement", counterparty_name="Acme")
    _read(w)
    _obligation(w, description="Name the period", metadata_json=NEEDS_PERSON)

    out = _list(w, group_by="counterparty")

    assert out["grouped_by"] == "counterparty" and set(out["groups"]) == {"Acme"}
    assert out["groups"]["Acme"][0]["date_state"] == "needs_person"
    assert out["needs_date"] == 1 and "NOT 'not due'" in out["summary"]


def test_a_person_who_may_not_open_a_contract_is_not_told_it_is_unread(w):
    """Guards the not-read list leaking another person's contract titles: it is filtered like every other list."""
    _contract(w, "Someone else's NDA")
    _with_text(w)
    stranger = User(org_id=w.org.id, email=f"{uuid.uuid4().hex[:8]}@example.com", full_name="Stranger",
                    hashed_password="hash", status="active")
    w.db.add(stranger)
    w.db.flush()

    assert [u["title"] for u in _list(w)["unread_contracts"]] == ["Someone else's NDA"]
    theirs = run._list_obligations(w.db, payload=ListObligationsInput(), user=stranger)
    assert theirs["unread_contracts"] == [] and "Someone else's NDA" not in theirs["summary"]


# --- my_attention_items --------------------------------------------------------------------------------------------


def test_attention_names_the_contracts_with_obligations_that_need_a_date(w):
    """Guards "nothing needs me" while the Obligations page shows a red "Needs a date" tile."""
    today = utcnow().date()
    _contract(w, "Services agreement")
    _read(w)
    _obligation(w, metadata_json=NEEDS_PERSON)
    _obligation(w)  # read before dates were counted
    _obligation(w, metadata_json=WAITING)  # not a gap
    _obligation(w, metadata_json=ONGOING)  # not a gap
    _obligation(w, due_date=today + timedelta(days=60))  # dated, outside the window

    out = _attention(w)

    assert [(i["reason"], i["urgency"], i["due_date"]) for i in out["items"]] == [
        ("Obligations need a date: 2 in Services agreement", "medium", None)]
    assert out["total"] == out["count"] == 1


def test_attention_names_active_contracts_whose_obligations_were_not_read_but_not_ones_being_read(w):
    _contract(w, "Failed one")
    _failed(w)
    _contract(w, "Never one")
    _with_text(w)
    _contract(w, "Running one")
    _db_job(w, _with_text(w), JobStatus.QUEUED)
    _contract(w, "Read one")
    _read(w)

    out = _attention(w)

    assert {i["title"]: i["reason"] for i in out["items"]} == {
        "Failed one": "Obligations not read: The AI's answer was cut off.",
        "Never one": "Obligations not read: they have not been read yet."}


def test_awaiting_signature_items_survive_the_cut_when_many_contracts_are_unread(w, monkeypatch):
    """Guards the new one-per-contract items pushing every "Awaiting signature" off the list: they tie on urgency and
    date, the sort is stable and the cut at 40 keeps insertion order, so they must be added after the signatures."""
    from app.obligations import assistant

    _contract(w, "Out for signature", stage="signature")
    _with_text(w)
    w.db.add(SignatureRequest(org_id=w.org.id, contract_id=w.contract.id,
                              contract_version_id=w.contract.current_authoritative_version_id, status="sent"))
    w.db.flush()
    many = [{"contract_id": f"c{i}", "title": f"NDA {i}", "reason": "Obligations not read: x", "due_date": None,
             "urgency": "medium"} for i in range(45)]
    monkeypatch.setattr(assistant, "unread_items", lambda db, user: many)

    out = _attention(w)

    assert out["total"] == 46 and len(out["items"]) == 40
    assert out["items"][0]["title"] == "Out for signature"


def test_a_dated_obligation_still_comes_before_the_new_attention_items(w):
    """Guards the new medium items outranking what is overdue: urgency decides first."""
    today = utcnow().date()
    _contract(w, "Services agreement")
    _read(w)
    _obligation(w, description="Pay up", due_date=today - timedelta(days=2), status="overdue")
    _obligation(w, metadata_json=NEEDS_PERSON)

    reasons = [i["reason"] for i in _attention(w)["items"]]

    assert reasons[0].startswith("Obligation overdue: Pay up") and reasons[1].startswith("Obligations need a date")


# --- extract_obligations -------------------------------------------------------------------------------------------


@pytest.fixture
def sent(monkeypatch):
    """The jobs the tool handed to the worker; nothing is really dispatched."""
    jobs = []
    monkeypatch.setattr(JobsService, "dispatch_job", lambda self, *, job: jobs.append(job))
    return jobs


def _extract(w):
    return run._extract_obligations(w.db, payload=ExtractObligationsInput(contract_id=w.contract.id), user=w.user,
                                    session_id="s-1")


def test_asking_for_a_contract_already_read_is_answered_not_queued_and_not_raised(w, sent):
    """Guards two failures: the reply "queued!" for an extraction that cannot run, and the refusal escaping as an
    exception. This tool always runs after the user confirms, where an exception ends the run FAILED and the model
    never hears why, so a 409 comes back as a result with a sentence to relay."""
    _contract(w, "Services agreement")
    _read(w)

    out = _extract(w)

    assert out["status"] == "not_started" and out["reason"] == "Obligations were already read for this version."
    assert "Nothing was queued" in out["summary"] and "do not say an extraction was started" in out["summary"]
    assert sent == [] and "job_id" not in out


def test_a_contract_with_no_readable_version_is_also_answered_not_raised(w, sent):
    _contract(w, "Services agreement")

    out = _extract(w)

    assert out["status"] == "not_started" and "no extractable authoritative version" in out["reason"] and sent == []


def test_a_conflict_that_is_not_a_refusal_to_start_still_raises(w, sent, monkeypatch):
    """Guards swallowing real errors: only the 409 refusals are answers, anything else is still an error."""
    _contract(w, "Services agreement")
    monkeypatch.setattr(ObligationsService, "extraction_job",
                        lambda self, **kw: (_ for _ in ()).throw(HTTPException(500, "boom")))

    with pytest.raises(HTTPException) as exc:
        _extract(w)

    assert exc.value.status_code == 500


def test_asking_while_a_reading_is_on_its_way_stacks_nothing_and_says_so(w, sent):
    """Guards paying twice: a second request while the first is queued returns that job, not a new one."""
    _contract(w, "Services agreement")
    job = _db_job(w, _with_text(w), JobStatus.QUEUED)

    out = _extract(w)

    assert out["status"] == "already_running" and out["job_id"] == job.id
    assert "Nothing new was queued" in out["summary"] and sent == []


def test_asking_for_a_contract_never_read_queues_one_job(w, sent):
    _contract(w, "Services agreement")
    _with_text(w)

    out = _extract(w)

    assert out["status"] == "queued" and "do not say the obligations are ready" in out["summary"]
    assert [j.id for j in sent] == [out["job_id"]]
    assert sent[0].idempotency_key.endswith(":assistant")


def test_the_job_is_committed_before_it_is_sent_to_the_worker(w, monkeypatch):
    """Guards the lost race: a worker that picks the message up before the row commits raises "Job not found", the
    row then commits QUEUED with a task id, and every later request sees a job "already running" that never starts."""
    events: list[str] = []
    monkeypatch.setattr(JobsService, "dispatch_job", lambda self, *, job: events.append("dispatch"))
    monkeypatch.setattr(w.db, "commit", lambda: events.append("commit"))
    _contract(w, "Services agreement")
    _with_text(w)

    assert _extract(w)["status"] == "queued"

    assert events == ["commit", "dispatch"]


def test_a_reader_may_not_start_an_extraction_through_the_assistant(w, sent, monkeypatch):
    """Guards the edit gate on this tool: starting an extraction rewrites the contract's obligations, which a person
    who may only read it must not do. Nothing is created or sent when the gate refuses."""
    from app.contracts.service import ContractService
    from app.jobs.models import JobRun

    _contract(w, "Services agreement")
    _with_text(w)
    seen = []

    def gate(self, *, contract_id, user, edit=False):
        seen.append(edit)
        if edit:
            raise HTTPException(403, "You can read this contract but not change it.")
        return w.contract

    monkeypatch.setattr(ContractService, "get_contract_for_user", gate)

    with pytest.raises(HTTPException) as exc:
        _extract(w)

    assert exc.value.status_code == 403 and True in seen and sent == []
    assert w.db.scalar(select(func.count(JobRun.id)).where(JobRun.resource_id == w.contract.id)) == 0


def _prior_call(w, tool, session_id=None, *, result=None):
    """A side-effecting tool call that succeeded a moment ago in a chat, under the key "k-1"."""
    if session_id is None:
        session = AssistantSession(org_id=w.org.id, created_by_user_id=w.user.id)
        w.db.add(session)
        w.db.flush()
        session_id = session.id
    w.db.add(AssistantToolCall(
        org_id=w.org.id, session_id=session_id, tool_name=tool, category="mutating", arguments={},
        status="succeeded", result=result or {"status": "queued"}, idempotency_key="k-1", started_at=utcnow()))
    w.db.flush()
    return session_id


def test_a_repeat_request_is_asked_again_not_answered_from_the_first_calls_queued(w):
    """Guards "did it work? run it again" being answered with the first call's stored "queued" for ten minutes: the
    job it queued has since finished or failed, so the repeat must reach the tool and get today's answer. Any other
    side-effecting tool is still replayed."""
    other = next(t.name for t in registry.tool_registry.all()
                 if t.category == "mutating" and t.name not in ("extract_obligations", "query_contracts"))
    session_id = _prior_call(w, "extract_obligations")
    _prior_call(w, other, session_id)

    def replayed(name):
        return run._replayed_call(w.db, spec=registry.tool_registry.get(name), org_id=w.org.id,
                                  session_id=session_id, idempotency_key="k-1")

    assert replayed("extract_obligations") is None
    assert replayed(other) == {"status": "queued"}


def test_asking_after_a_failed_reading_restarts_that_job_instead_of_adding_another(w, sent):
    """Guards a pile of failed jobs for one version: Retry and the assistant restart the one that ended badly."""
    _contract(w, "Services agreement")
    failed = _db_job(w, _with_text(w), JobStatus.FAILED, attempt_count=3, finished_at=datetime.now(UTC))

    out = _extract(w)

    assert out["status"] == "queued" and out["job_id"] == failed.id and sent == [failed]


# --- the model is told ---------------------------------------------------------------------------------------------


def test_the_tool_descriptions_tell_the_model_what_to_read_before_answering():
    """Guards the sentences being lost in a rewrite: the model cannot use `summary` and `unread_contracts` unless the
    description says to. The system prompt is untouched."""
    spec = registry.tool_registry.get
    assert "summary" in spec("list_obligations").description and "unread_contracts" in spec("list_obligations").description
    assert "need a date" in spec("my_attention_items").description
    assert "not report those as queued" in spec("extract_obligations").description
