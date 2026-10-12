"""The exam's ledger copy and crash reconciliation.

specs/005-ai-eval-baseline T013 (FR-34, FR-38, FR-39, FR-40, FR-41; AC-34, AC-38, AC-40). The exam
keeps its call records in its own throwaway database and copies only metering columns into the
live ledger. Both databases here are scratch Postgres databases holding just the tables involved
(see ``tests/test_exam_gate.py``), so the stamping UPDATE -- which tags every record that has no
run id -- can never touch the shared dev database's real usage rows. The "exam" one is named
``legal_clm_eval_...`` because the stamp refuses any other database. The full two-database path
through a real run is covered by the isolation test.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.ai.gateway.features import feature_registry
from app.ai.gateway.ledger import AICallContext, record_call
from app.ai.prompt_versions import PromptBundle
from app.analytics.routes import token_cost
from app.core.config import settings
from app.core.database import Base
from app.core.models import AICallLog
from app.integrations.claude import ClaudeProviderResponse
from eval.exam import runner
from eval.exam.env import EVAL_DB_NAME, ExamRefused, LiveDB, Owner
from tests.test_exam_gate import (
    OWNER,
    SCRATCH_TABLES,
    Typist,
    approve,
    audit_rows,
    make_estimate,
    make_live,
    scratch_database,
)

EXAM_ORG = "exam-org-id"
EXAM_USER = "exam-runner-user-id"
SECRET = "THE CONTRACT SAYS SOMETHING PRIVATE"


@contextmanager
def exam_scratch_database():
    """Like ``scratch_database`` but named the way the exam's own database is, because
    ``stamp_and_mirror`` refuses to tag call records anywhere else."""
    base = make_url(settings.database_url)
    name = f"{EVAL_DB_NAME}_scratch_{uuid.uuid4().hex[:10]}"
    admin = create_engine(
        base.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except SQLAlchemyError as exc:
        admin.dispose()
        pytest.skip(f"this database user cannot CREATE DATABASE ({type(exc).__name__})")
    engine = create_engine(base.set(database=name), poolclass=NullPool)
    try:
        Base.metadata.create_all(engine, tables=[Base.metadata.tables[t] for t in SCRATCH_TABLES])
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def live_engine():
    with scratch_database() as engine:
        yield engine


@pytest.fixture(scope="module")
def exam_engine():
    with exam_scratch_database() as engine:
        yield engine


@pytest.fixture
def live(live_engine) -> LiveDB:
    return make_live(live_engine)


@pytest.fixture
def exam(exam_engine) -> sessionmaker:
    """The exam database's sessions, with an empty ledger: each test is 'a fresh run'."""
    with exam_engine.begin() as conn:
        conn.execute(text("DELETE FROM a_i_call_log"))
    return sessionmaker(bind=exam_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(autouse=True)
def runs_dir(tmp_path, monkeypatch):
    """Point the run folder at a temp dir: the real eval/private/runs must never be read."""
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)
    return tmp_path


def call(**changes) -> AICallLog:
    """An exam-database call record as the app's gateway writes it, contract text included."""
    values = dict(
        org_id=EXAM_ORG,
        created_by_user_id=EXAM_USER,
        updated_by_user_id=EXAM_USER,
        provider="claude",
        model="claude-sonnet-4-6",
        model_config_hash="cfg",
        prompt_key="playbook_review",
        prompt_version="3",
        prompt_hash="hash",
        output_schema_name="PlaybookReviewOutput",
        prompt_tokens=1000,
        completion_tokens=200,
        total_tokens=1200,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=40,
        latency_ms=1234.5,
        status="succeeded",
        validation_status="valid",
        stop_reason="tool_use",
        provider_request_id="req_plain",
        resource_type="contract",
        resource_id="exam-contract-id",
        request_id="rv-cuad-001",
        job_id="exam-job-id",
        skill_run_id="exam-skill-run-id",
        session_id="exam-session-id",
        assistant_run_id="exam-assistant-run-id",
        tool_call_id="exam-tool-call-id",
        input_payload={"user_prompt": SECRET},
        validated_output={"quote": SECRET},
        raw_ai_output={"content": SECRET},
        validation_error=SECRET,
    )
    values.update(changes)
    return AICallLog(**values)


def add_all(sessions: sessionmaker, *rows: AICallLog) -> list[str]:
    with sessions() as db:
        db.add_all(rows)
        db.commit()
        return [r.id for r in rows]


def stamp(run_id, exam, live):
    return runner.stamp_and_mirror(run_id=run_id, exam_sessions=exam, live=live, owner=OWNER)


def live_rows(live: LiveDB, **filters) -> list[AICallLog]:
    with live.sessions() as db:
        return list(db.scalars(select(AICallLog).where(AICallLog.org_id == live.org_id)))


# --- mirror_values: what may reach the live ledger (FR-34, FR-38) ---------------------------

# Columns that are deliberately NOT copied (they hold contract text or exam-database ids), and
# the three that are rewritten for the live database.
NOT_COPIED = {
    "input_payload", "validated_output", "raw_ai_output", "validation_error", "request_id",
    "job_id", "skill_run_id", "session_id", "assistant_run_id", "tool_call_id", "resource_id",
    "redaction_status",
}
REWRITTEN = {"org_id", "created_by_user_id", "updated_by_user_id"}


def test_every_ledger_column_is_either_copied_blanked_or_rewritten():
    """The copy is a whitelist so that a column added to the ledger later does NOT leak into the
    live database by default. Adding a column fails this test until someone decides, here and in
    ``_MIRRORED_COLUMNS``, whether it may be copied."""
    columns = {c.name for c in AICallLog.__table__.columns}
    assert columns == set(runner._MIRRORED_COLUMNS) | NOT_COPIED | REWRITTEN
    assert not set(runner._MIRRORED_COLUMNS) & (NOT_COPIED | REWRITTEN)


def test_the_live_copy_keeps_metering_and_nothing_else():
    """FR-38: the live ledger gets ids and numbers only. No prompt, answer, quote, error text or
    exam-database id may survive, and org and actor become the live org and the owner."""
    row = call(eval_run_id="run-1", batched=True)
    values = runner.mirror_values(row, live_org_id="live-org", owner_id="owner-id")
    assert not NOT_COPIED & values.keys()
    assert SECRET not in json.dumps(values, default=str)
    assert "exam-contract-id" not in json.dumps(values, default=str)
    assert values["org_id"] == "live-org"
    assert values["created_by_user_id"] == values["updated_by_user_id"] == "owner-id"
    assert (values["eval_run_id"], values["batched"], values["resource_type"]) == (
        "run-1", True, "contract")
    assert (values["prompt_tokens"], values["completion_tokens"], values["cache_read_input_tokens"]) == (
        1000, 200, 40)
    assert values["model"] == "claude-sonnet-4-6" and values["latency_ms"] == 1234.5
    assert runner.mirror_values(call(eval_run_id="r"), live_org_id="o", owner_id="u")["batched"] is False


def test_an_unstamped_record_is_never_copied_as_real_usage():
    """A copied record without eval_run_id would show up in real-usage cost figures (FR-34):
    refuse rather than copy it."""
    with pytest.raises(ValueError, match="stamp"):
        runner.mirror_values(call(), live_org_id="o", owner_id="u")


# --- stamp_and_mirror (FR-34, FR-41) ---------------------------------------------------------


def test_stamping_tags_only_unstamped_rows_and_marks_batch_answers(exam, live):
    """Only rows with no run id are this run's; a row already tagged for another run stays as it
    was. A row answered through a batch (replayed id 'msgbatch…:<custom_id>') is marked batched,
    so the usage page prices it at half; a failed call with no provider id is not."""
    plain, batched, failed, foreign = add_all(
        exam,
        call(provider_request_id="req_1"),
        call(provider_request_id="msgbatch_01abc:" + "f" * 64),
        call(provider_request_id=None, status="failed", prompt_tokens=None, completion_tokens=None),
        call(provider_request_id="req_2", eval_run_id="another-run"),
    )
    assert stamp("run-1", exam, live) == 3
    with exam() as db:
        by_id = {r.id: (r.eval_run_id, r.batched) for r in db.scalars(select(AICallLog))}
    assert by_id == {
        plain: ("run-1", False),
        batched: ("run-1", True),
        failed: ("run-1", False),
        foreign: ("another-run", False),
    }
    copied = {r.id: r.batched for r in live_rows(live)}
    assert copied == {plain: False, batched: True, failed: False}  # nothing of 'another-run'
    assert all(r.eval_run_id == "run-1" for r in live_rows(live))  # none counts as real usage


def test_the_live_copy_has_no_contract_text_and_no_exam_ids_in_the_database(exam, live):
    """FR-38 against a real database: the columns that held text and exam ids are SQL NULL in the
    live row (not a JSON 'null'), while the metering columns arrived intact under the live org."""
    [row_id] = add_all(exam, call(provider_request_id="msgbatch_01abc:def"))
    stamp("run-1", exam, live)
    with live.sessions() as db:
        nulls = db.execute(
            text(
                "SELECT input_payload IS NULL, validated_output IS NULL, raw_ai_output IS NULL, "
                "validation_error IS NULL, request_id IS NULL, job_id IS NULL, "
                "skill_run_id IS NULL, session_id IS NULL, assistant_run_id IS NULL, "
                "tool_call_id IS NULL, resource_id IS NULL "
                "FROM a_i_call_log WHERE id = :id"
            ),
            {"id": row_id},
        ).one()
        copy = db.get(AICallLog, row_id)
    assert all(nulls)
    assert copy.org_id == live.org_id and copy.created_by_user_id == OWNER.user_id
    assert copy.updated_by_user_id == OWNER.user_id
    assert (copy.prompt_key, copy.prompt_version, copy.model) == (
        "playbook_review", "3", "claude-sonnet-4-6")
    assert (copy.prompt_tokens, copy.completion_tokens, copy.total_tokens) == (1000, 200, 1200)
    assert copy.cache_read_input_tokens == 40 and copy.latency_ms == 1234.5
    assert copy.status == "succeeded" and copy.stop_reason == "tool_use"
    assert copy.provider_request_id == "msgbatch_01abc:def" and copy.batched is True
    assert copy.resource_type == "contract" and copy.eval_run_id == "run-1"


def test_stamping_refuses_a_session_that_is_not_the_exam_database(live):
    """FR-34: the stamp tags EVERY record without a run id. Pointed at the live database (a
    mis-wired session, a wrong name) those records are real usage, and tagging them would turn
    real spend into "exam traffic" and hide it from the usage page. It must refuse, change
    nothing, and copy nothing."""
    with live.sessions() as db:
        db.add(call(org_id=live.org_id, eval_run_id=None, provider_request_id="req_real"))
        db.commit()
    with pytest.raises(ExamRefused, match="legal_clm_eval"):
        runner.stamp_and_mirror(
            run_id="run-1", exam_sessions=live.sessions, live=live, owner=OWNER
        )
    [row] = live_rows(live)
    assert (row.eval_run_id, row.batched) == (None, False)


def test_copying_again_adds_only_what_is_new(exam, live):
    """The copy runs after every unit and again in crash recovery, so it must be repeatable: a
    second pass copies nothing, a new record is added once, and no row is ever duplicated."""
    add_all(exam, call(), call())
    assert stamp("run-1", exam, live) == 2
    assert stamp("run-1", exam, live) == 0
    add_all(exam, call())
    assert stamp("run-1", exam, live) == 1
    assert len(live_rows(live)) == 3


def test_a_big_run_is_copied_in_chunks(exam, live, monkeypatch):
    """One INSERT cannot carry unlimited rows (a statement holds at most 65,535 parameters), so
    a full run's ledger is copied in chunks; every row must still arrive exactly once."""
    monkeypatch.setattr(runner, "_MIRROR_CHUNK", 2)
    add_all(exam, *(call() for _ in range(5)))
    assert stamp("run-1", exam, live) == 5
    assert len(live_rows(live)) == 5


def test_rows_stamped_but_never_copied_are_copied_on_the_next_pass(exam, live):
    """If the process died between the stamp and the copy, the rows are tagged in the exam
    database but absent from live; the next pass (crash recovery) must find and copy them."""
    ids = add_all(exam, call(eval_run_id="run-1", batched=False), call(eval_run_id="run-1"))
    assert live_rows(live) == []
    assert stamp("run-1", exam, live) == 2
    assert {r.id for r in live_rows(live)} == set(ids)


def test_an_exam_record_never_appears_in_real_usage_in_the_live_ledger(exam, live):
    """FR-34 at the database: after the copy, the live ledger's 'real' rows (no run id) for this
    org are exactly the rows that were already there, i.e. none of the exam's."""
    with live.sessions() as db:
        db.add(call(org_id=live.org_id, eval_run_id=None, provider_request_id="req_real"))
        db.commit()
    add_all(exam, call(), call())
    stamp("run-1", exam, live)
    with live.sessions() as db:
        real = db.scalars(select(AICallLog).where(AICallLog.org_id == live.org_id,
                                                  AICallLog.eval_run_id.is_(None))).all()
        exam_rows = db.scalars(select(AICallLog).where(AICallLog.org_id == live.org_id,
                                                       AICallLog.eval_run_id == "run-1")).all()
    assert [r.provider_request_id for r in real] == ["req_real"] and len(exam_rows) == 2


def test_direct_calls_are_written_with_their_run_id_and_app_calls_without(live):
    """Pre-fill and routing drafting write the LIVE ledger directly (no copy step): their rows
    must carry eval_run_id at write time, while an app call (a context without one) stays NULL --
    or real usage would lose its rows."""
    response = ClaudeProviderResponse(
        raw_response={}, content_blocks=[], tool_use_blocks=[], stop_reason="end_turn",
        token_usage={"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
        latency_ms=5.0, provider_request_id="req_x", model="claude-sonnet-5-5",
    )

    def write(ctx):
        return record_call(
            sessions=live.sessions, feature=feature_registry.get("flow_router"), ctx=ctx,
            prompt=PromptBundle("flow_router", "1", "h", "s", "p", "m", "c"),
            model="claude-sonnet-5-5", log_input={"x": 1}, status="succeeded",
            validation_status="valid", response=response,
        ).id

    exam_id = write(AICallContext(org_id=live.org_id, user_id=OWNER.user_id, eval_run_id="run-9"))
    real_id = write(AICallContext(org_id=live.org_id, user_id=OWNER.user_id))
    with live.sessions() as db:
        assert db.get(AICallLog, exam_id).eval_run_id == "run-9"
        assert db.get(AICallLog, real_id).eval_run_id is None
        assert db.get(AICallLog, exam_id).batched is False


# --- ledger_actual: what a run really cost -------------------------------------------------


def test_ledger_actual_prices_with_the_usage_pages_rates_and_halves_batch_rows(live):
    """FR-33/FR-41: the run record's actual cost must be the usage page's own price table, with
    batch rows at half and list price shown beside it; failed calls are counted, not hidden."""
    with live.sessions() as db:
        db.add_all([
            call(org_id=live.org_id, eval_run_id="run-1", prompt_tokens=2000, completion_tokens=500),
            call(org_id=live.org_id, eval_run_id="run-1", prompt_tokens=2000, completion_tokens=500,
                 batched=True, prompt_key="contract_metadata_extraction"),
            call(org_id=live.org_id, eval_run_id="run-1", status="failed", prompt_tokens=None,
                 completion_tokens=None, model="claude-sonnet-5-5"),
            call(org_id=live.org_id, eval_run_id="other-run", prompt_tokens=9_000_000),
        ])
        db.commit()
    actual = runner.ledger_actual(live.sessions, "run-1", org_id=live.org_id)
    plain = token_cost(2000, 500, "claude-sonnet-4-6", 40)
    assert actual["usd"] == pytest.approx(plain + plain / 2, abs=1e-5)
    assert actual["list_usd"] == pytest.approx(plain * 2, abs=1e-5)
    assert (actual["calls"], actual["failed_calls"]) == (3, 1)
    assert actual["by_call_type"]["playbook_review"]["calls"] == 2
    assert actual["by_call_type"]["contract_metadata_extraction"] == {
        "calls": 1, "batch": 1, "usd": pytest.approx(plain / 2, abs=1e-5)}
    assert actual["ocr_usd"] is None and actual["ocr_credits"] is None


# --- reconcile_crashed_runs (FR-40, AC-40) ----------------------------------------------------


def start_run(live, kind="exam", ceiling="25", **extra):
    run = approve(live, ask=Typist(ceiling=ceiling), kind=kind, extra=extra or None)
    assert run is not None
    return run


def put_state(runs_dir, run_id, step):
    (runs_dir / run_id).mkdir()
    (runs_dir / run_id / "state.json").write_text(json.dumps({"run_id": run_id, "step": step}))


def test_a_run_that_never_finished_is_recorded_as_incomplete_with_its_cost(live):
    """AC-40: a crash part-way leaves an approval and no finish. Reconciliation writes the one
    immutable record -- incomplete, crashed, the cost its ledger rows show, the errors flagged --
    so the run can never be mistaken for a clean baseline run."""
    run = start_run(live, mode="full", versions_at_start={"model_under_test": "claude-sonnet-4-6"})
    with live.sessions() as db:
        db.add_all([
            call(org_id=live.org_id, eval_run_id=run.run_id, provider_request_id="msgbatch_x:y",
                 batched=True),
            call(org_id=live.org_id, eval_run_id=run.run_id, status="failed"),
        ])
        db.commit()
    assert runner.reconcile_crashed_runs(live, exam_sessions=None) == [run.run_id]
    [row] = audit_rows(live, "eval.run_finished")
    after = row.after
    assert row.resource_id == run.run_id and row.actor_user_id == OWNER.user_id
    assert (after["outcome"], after["incomplete_reason"]) == ("incomplete", "crashed")
    assert after["kind"] == "exam" and after["mode"] == "full" and after["ceiling_usd"] == 25.0
    assert after["versions"] == {"model_under_test": "claude-sonnet-4-6"}
    assert after["actual"]["calls"] == 2 and after["actual"]["failed_calls"] == 1
    assert after["actual"]["usd"] > 0
    assert after["contains_errors"] is True and after["failed_call_rate"] == 0.5
    assert after["results_sha256"] is None and after["estimate"]["worst_case_usd"] == 24.5
    assert after["started_at"] and after["finished_at"]


def test_reconciling_twice_writes_one_record(live):
    """The record is permanent, so recovery must be idempotent: the second pass finds nothing."""
    run = start_run(live)
    assert runner.reconcile_crashed_runs(live, exam_sessions=None) == [run.run_id]
    assert runner.reconcile_crashed_runs(live, exam_sessions=None) == []
    assert len(audit_rows(live, "eval.run_finished")) == 1


def test_a_run_that_finished_normally_is_left_alone(live):
    """Only runs with no finish record are crashed: a normal finish must not be second-guessed."""
    run = start_run(live)
    runner.finish_paid_run(run, outcome="complete", reason=None, actual={}, results_sha256="a" * 64,
                           summary=None)
    assert runner.reconcile_crashed_runs(live, exam_sessions=None) == []
    [row] = audit_rows(live, "eval.run_finished")
    assert row.after["outcome"] == "complete"


@pytest.mark.parametrize("step", ["submitted", "retry_submitted", "submitting", "submit_failed"])
def test_a_run_waiting_on_a_batch_is_not_called_crashed(live, runs_dir, step):
    """A batch may be outstanding (and billing). Marking its run finished would leave a batch
    nobody can replay or cancel, so --resume / --abandon must still be able to find the run."""
    run = start_run(live)
    put_state(runs_dir, run.run_id, step)
    assert runner.reconcile_crashed_runs(live, exam_sessions=None) == []
    assert audit_rows(live, "eval.run_finished") == []


@pytest.mark.parametrize("step", ["captured", "replayed", "ingested"])
def test_a_run_with_no_batch_outstanding_is_crashed(live, runs_dir, step):
    """The contrast: before a batch was submitted, or after it was replayed, a dead process is a
    crashed run, not a waiting one."""
    run = start_run(live)
    put_state(runs_dir, run.run_id, step)
    assert runner.reconcile_crashed_runs(live, exam_sessions=None) == [run.run_id]


def test_an_unreadable_state_file_does_not_keep_a_run_open_forever(live, runs_dir):
    """Nothing can resume a run whose state is unreadable, so it is closed as crashed."""
    run = start_run(live)
    (runs_dir / run.run_id).mkdir()
    (runs_dir / run.run_id / "state.json").write_text("{not json")
    assert runner.reconcile_crashed_runs(live, exam_sessions=None) == [run.run_id]


def test_leftover_exam_rows_are_stamped_for_the_newest_exam_run_and_copied_first(live, exam):
    """Rows written after the last per-unit stamp belong to the run that was running -- the newest
    unfinished exam run, since the exam database is rebuilt for each run. They are stamped and
    copied before the record is written, so its cost includes them; an older crashed run gets none."""
    older = start_run(live)
    newer = start_run(live)
    add_all(exam, call(provider_request_id="msgbatch_x:y"), call())
    assert runner.reconcile_crashed_runs(live, exam_sessions=exam) == [older.run_id, newer.run_id]
    finished = {r.resource_id: r.after for r in audit_rows(live, "eval.run_finished")}
    assert finished[newer.run_id]["actual"]["calls"] == 2
    assert finished[older.run_id]["actual"]["calls"] == 0
    with exam() as db:
        assert {r.eval_run_id for r in db.scalars(select(AICallLog))} == {newer.run_id}


def test_other_kinds_of_run_do_not_claim_the_exam_databases_rows(live, exam):
    """Pre-fill writes the live ledger directly and never the exam database, so an unfinished
    pre-fill run must not be handed whatever sits unstamped in the exam database."""
    run = start_run(live, kind="prefill")
    add_all(exam, call())
    assert runner.reconcile_crashed_runs(live, exam_sessions=exam) == [run.run_id]
    with exam() as db:
        assert db.scalars(select(AICallLog.eval_run_id)).all() == [None]


def test_a_missing_exam_database_does_not_block_recovery(live):
    """The exam database is deleted whenever the owner likes (and does not exist before the first
    run). Recovery must still close the run, taking its cost from what was already copied."""
    base = make_url(settings.database_url)
    gone = create_engine(base.set(database=f"legal_clm_missing_{uuid.uuid4().hex[:8]}"),
                         poolclass=NullPool)
    try:
        run = start_run(live)
        sessions = sessionmaker(bind=gone)
        assert runner.reconcile_crashed_runs(live, exam_sessions=sessions) == [run.run_id]
    finally:
        gone.dispose()
    assert len(audit_rows(live, "eval.run_finished")) == 1


def test_another_organisations_runs_are_not_reconciled(live, live_engine):
    """Everything is org-scoped: reconciliation must never write a record for another org's run."""
    other = make_live(live_engine)
    theirs = start_run(other)
    mine = start_run(live)
    assert runner.reconcile_crashed_runs(live, exam_sessions=None) == [mine.run_id]
    assert audit_rows(other, "eval.run_finished") == []
    assert theirs.run_id not in [r.resource_id for r in audit_rows(live, "eval.run_finished")]


def test_the_owner_on_the_record_is_the_one_who_approved_the_run(live):
    """The reconciled record names the approver from the approval row, not whoever happens to be
    running the next command."""
    approver = Owner(user_id="the-approver", email="approver@example.com")
    run = runner.approve_paid_run(kind="exam", live=live, owner=approver, estimate=make_estimate(),
                                  ask=Typist(), extra=None)
    runner.reconcile_crashed_runs(live, exam_sessions=None)
    [row] = audit_rows(live, "eval.run_finished")
    assert row.resource_id == run.run_id and row.actor_user_id == "the-approver"
    assert row.after["approved_by"] == {"user_id": "the-approver", "email": "approver@example.com"}


# --- the kind of a paid run (FR-39) -------------------------------------------------------------


@pytest.mark.parametrize("kind", ["exam", "ocr", "prefill", "draft_routing"])
def test_every_kind_of_paid_run_the_exam_has_is_approved(live, kind):
    """FR-39: the four kinds the exam's commands pass are the whole set; a guard that rejected
    one of them would stop that command before it could be approved."""
    typist = Typist()
    run = approve(live, ask=typist, kind=kind)
    assert run is not None and run.kind == kind
    assert audit_rows(live, "eval.spend_approved")[0].after["kind"] == kind


@pytest.mark.parametrize("kind", ["draft-routing", "Exam", "", "reconcile"])
def test_an_unknown_kind_of_paid_run_is_refused_before_anything_is_asked_or_written(live, kind):
    """FR-39: the kind goes into an audit row that can never be edited, and decides whether crash
    recovery claims the exam database's rows. A misspelt one ("draft-routing" for
    "draft_routing") must fail loudly before the owner is asked for a go-ahead, not leave a
    permanent record nobody can read back."""
    typist = Typist()
    with pytest.raises(ValueError, match="run kind"):
        approve(live, ask=typist, kind=kind)
    assert typist.prompts == []
    assert audit_rows(live) == []
