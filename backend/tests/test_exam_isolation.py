"""The exam cannot reach real data, real people or the live app's money: proved end to end.

specs/005-ai-eval-baseline T019 (FR-27, FR-28, FR-30, FR-31, FR-34, FR-37, FR-38; AC-31, AC-34,
AC-37, AC-38, AC-39, AC-41). The unit tests around ``eval/exam`` prove each guard on its own; this
file proves they hold TOGETHER, in the one place that matters: a fresh process that starts the way
``python -m eval.exam`` does. That has to be a subprocess. The guarantee is that the exam's process
begins on the throwaway database, and pytest's own process cannot show it: the app's engine was
built long ago and points at the development database.

``tests/exam_isolation_driver.py`` is that process. It runs four complete exams (batch delivery)
of a small generated exam, through the real isolation, the real owner gate, the real rebuild of
``legal_clm_eval...`` and the app's real code on the far side of a fake Anthropic client, then
runs the command line's own ``baseline set`` and ``compare`` on the records they left. The
"live" database is a scratch one built here: the full schema, an organisation, the owner, two live
contracts and one live workflow. Nothing in this file or the driver spends money or reaches the
network (the driver refuses every connection and counts the attempts), and no contract text is
real.

The command-line tests below it start the real ``python -m eval.exam`` and check the refusals that
must happen before anything else does.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.auth.models import User
from app.contracts.models import Contract
from app.core.config import settings
from app.organizations.models import Organization
from eval.exam import __main__ as cli
from eval.exam.content import OWNER_FILES, Task
from eval.exam.env import EVAL_DB_NAME, ExamRefused
from tests.test_exam_batch import scratch
from tests.test_exam_gate import PASSWORD, PASSWORD_HASH, make_live, scratch_database

BACKEND = Path(__file__).resolve().parent.parent  # backend/ (/app in the container)
OWNER_EMAIL = "iso-owner@example.com"
MARKER = "ISOLATION_RESULT "

# What the exam may do to the live database (FR-34, FR-38, FR-39): copy ledger rows and write
# audit rows. Reads: the organisation and the owner (the gate), the workflow list (routing).
LIVE_WRITES = {"a_i_call_log", "audit_log"}
LIVE_TABLES_USED = {"a_i_call_log", "audit_log", "organization", "user", "workflow"}


def server_engine():
    return create_engine(settings.database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")


def databases() -> set[str]:
    engine = server_engine()
    try:
        with engine.connect() as conn:
            return set(conn.execute(text("SELECT datname FROM pg_database")).scalars())
    finally:
        engine.dispose()


def drop_exam_database(name: str) -> None:
    # The only DROP in this file: it can never name anything but a test's exam database.
    assert name.startswith(f"{EVAL_DB_NAME}_iso_"), name
    engine = server_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        engine.dispose()


def process_env(live_engine, **extra: str) -> dict[str, str]:
    """The environment of an exam process: the scratch database as "live", the owner configured,
    and no API key of any kind inherited (the dev container carries real ones)."""
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env |= {
        "DATABASE_URL": live_engine.url.render_as_string(hide_password=False),
        "EVAL_OWNER_EMAIL": OWNER_EMAIL,
        # A real owner's private exam.env must not leak into a test: an empty value wins over it.
        "EVAL_CLAUDE_API_KEY": "",
    }
    return env | extra


def seed_live(engine) -> None:
    """What a live database has that the exam reads or must leave alone: an organisation, the
    owner (with a password the tests know) and two live contracts."""
    with sessionmaker(bind=engine)() as db:
        org = Organization(name="Live org", slug=f"live-{uuid.uuid4().hex[:8]}")
        db.add(org)
        db.flush()
        owner = User(
            org_id=org.id,
            email=OWNER_EMAIL,
            full_name="Exam owner",
            hashed_password=PASSWORD_HASH,
            status="active",
        )
        db.add(owner)
        db.flush()
        db.add_all(
            Contract(org_id=org.id, title=f"Live contract {n}", owner_user_id=owner.id)
            for n in (1, 2)
        )
        db.commit()


@pytest.fixture(scope="module")
def live_engine():
    """A scratch 'live' database for the whole-exam driver: the full schema, seeded. Dropped
    afterwards; the shared development database is never touched."""
    with scratch("legal_clm_scratch_iso") as engine:
        seed_live(engine)
        yield engine


@pytest.fixture(scope="module")
def cli_engine():
    """A second, untouched 'live' database for the command-line tests, so what the driver wrote to
    its own (audit rows, a baseline) cannot make them depend on the order they run in."""
    with scratch("legal_clm_scratch_cli") as engine:
        seed_live(engine)
        yield engine


# =============================================================================================
# The whole exam, in a fresh process
# =============================================================================================


@pytest.fixture(scope="module")
def ran(live_engine, tmp_path_factory):
    """The driver's observations of four complete batch-delivery exams, parsed. The driver's own
    exit code is part of the proof: a failure anywhere (a refusal, a crash, an assertion in the
    app's own code) fails every test below with the driver's output."""
    exam_name = f"{EVAL_DB_NAME}_iso_{uuid.uuid4().hex[:8]}"
    workdir = tmp_path_factory.mktemp("isolation")
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.exam_isolation_driver",
                str(workdir),
                exam_name,
                OWNER_EMAIL,
                PASSWORD,
            ],
            cwd=BACKEND,
            env=process_env(live_engine),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    finally:
        drop_exam_database(exam_name)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
    [line] = [x for x in proc.stdout.splitlines() if x.startswith(MARKER)]
    result = json.loads(line[len(MARKER) :])
    result["exam_name"] = exam_name
    return result


def test_the_exam_process_runs_on_the_exam_database_not_the_live_one(ran):
    """FR-37: the app's own engine, the one every ``SessionLocal()`` in app code opens, was moved
    onto ``legal_clm_eval...`` before anything imported it. If it had stayed on the live database,
    any code path that opens its own session (the ledger, triage, the authz log) would have put
    exam rows there."""
    assert ran["engine_database"] == ran["exam_name"] != ran["live_database"]
    assert ran["engine_database"].startswith(EVAL_DB_NAME)


def test_four_runs_complete_and_every_item_is_scored(ran):
    """The control for everything below: the exams really ran (all five kinds of item, an owner
    contract, two batches each), so "nothing leaked" is not "nothing happened"."""
    assert len(ran["runs"]) == 4
    for run in ran["runs"]:
        assert run["outcome"] == "complete" and run["delivery"] == "batch"
        assert set(run["items"]) == {
            "rv-cp-i1",
            "ex-cuad-i1",
            "ex-owner-12",
            "qa-001",
            "pq-01",
            "rt-01",
        }
        assert set(run["items"].values()) == {"scored"}
        assert len(run["batch_sizes"]) == 2 and sum(run["batch_sizes"]) > 0


def test_live_contracts_and_every_table_but_the_ledger_and_the_audit_log_are_untouched(ran):
    """FR-38/AC-38: after four full exams, each reading three contracts and one owner contract
    into a database it rebuilt from nothing, the live database has the same rows in every table
    it started with, except the two the exam is allowed to write: AI call records and audit rows."""
    before, after = ran["live_counts_before"], ran["live_counts_after"]
    assert before["contract"] == after["contract"] == 2
    assert ran["live_contract_titles"] == ["Live contract 1", "Live contract 2"]
    changed = {t for t in before if before[t] != after[t]}
    assert changed == LIVE_WRITES
    assert after["contract_text_snapshot"] == after["contract_embedding"] == 0
    assert after["notification"] == after["workflow_run"] == after["job_run"] == 0


def test_the_exams_own_live_connection_only_touches_what_the_plan_allows(ran):
    """FR-38/FR-39: every SQL statement the exam sent to the live database named only the gate's
    tables (organisation, user), the workflow list it routes against, the ledger and the audit
    log. A statement that named ``contract`` or anything else would be a way for exam data to get
    there."""
    assert set(ran["live_statement_tables"]) <= LIVE_TABLES_USED
    assert {"audit_log", "a_i_call_log"} <= set(ran["live_statement_tables"])


def test_the_live_ledger_gets_marked_exam_calls_without_their_text(ran):
    """FR-34/FR-41/AC-34: what reaches the live ledger is the cost record of each call, stamped
    with its run so real usage can be told apart from exam traffic, flagged batched exactly when
    it was answered by a batch, and carrying no prompt, answer or exam-database id."""
    run_ids = {r["run_id"] for r in ran["runs"]}
    rows = ran["live_ledger"]
    assert rows and {r["eval_run_id"] for r in rows} == run_ids
    assert not any(r["has_text"] for r in rows)
    assert all(r["batched"] == r["provider_request_id"].startswith("msgbatch") for r in rows)
    assert any(r["batched"] for r in rows) and any(not r["batched"] for r in rows)


def test_the_live_audit_log_gets_exam_records_and_nothing_else(ran):
    """FR-39: the only audit rows the exam wrote are its own ``eval.*`` records: one approval and
    one finished-run record per run, and the two baseline designations."""
    actions = [a["action"] for a in ran["live_audit"]]
    assert set(actions) == {"eval.spend_approved", "eval.run_finished", "eval.baseline_designated"}
    assert [actions.count(a) for a in ("eval.spend_approved", "eval.run_finished")] == [4, 4]
    assert actions.count("eval.baseline_designated") == 2


def test_nothing_is_dispatched_mailed_or_notified_and_no_connection_is_opened(ran):
    """FR-38/AC-38: across four runs no Celery task was sent, no job dispatched, no stage
    transition (whose triggers start jobs and workflows) made, no workflow started, no e-mail
    handed to a transport, and Python opened no network connection at all."""
    # Controls first: the recorders would have seen a send and a connection had there been one.
    assert ran["control_dispatch_seen"] and ran["control_connection_seen"]
    assert ran["side_effects"] == []
    assert ran["refused_connections"] == []
    for run in ran["runs"]:
        exam = run["exam"]
        assert exam["notifications"] == exam["workflow_runs"] == exam["stage_history"] == 0
        assert exam["intake_requests"] == 0  # triage reads a request that is never saved


def test_exam_contracts_exist_only_in_the_exam_database_and_questions_search_only_those(ran):
    """FR-37/AC-37: the exam's contracts, their answers and every Contract Brain search live in
    ``legal_clm_eval...``; the live database (which holds contracts of its own) has none of them,
    and each question was answered from the exam's contracts and no others."""
    titles = {"cp-i1", "cuad-i1", "pf-i1", Path(OWNER_FILES[11]).stem}
    for run in ran["runs"]:
        exam = run["exam"]
        assert set(exam["contracts"]) == titles
        assert exam["brain_queries"] == 2
        searched = set(exam["brain_query_contract_ids"])
        assert searched and searched <= set(exam["contracts"].values())
        assert exam["users"] == ["exam-portfolio@example.com", "exam-runner@example.com"]
    assert not titles & set(ran["live_contract_titles"])


def test_the_owners_contract_is_called_normally_and_never_appears_in_a_batch(ran):
    """FR-35/FR-41/AC-41: real contracts are never batched. The owner contract's clause labelling
    and metadata went out as two ordinary calls and its text is in no batch request; everything
    else went in the two batches -- except the portfolio question, whose filter call and search
    answer are ordinary calls too (a "which contracts" question makes several calls, and a batch
    replays one per question)."""
    for run in ran["runs"]:
        assert run["direct_calls"] == 4
        assert run["owner_text_sent_directly"] is True
        assert run["owner_text_in_batches"] is False


def test_dropping_the_exam_database_and_running_again_rebuilds_it_from_nothing(ran):
    """FR-38/AC-38: the exam database is disposable. With it dropped from outside, the next run
    makes it again with the same contents under new ids, and a run on top of an existing one does
    not pile rows up (every run is on a database rebuilt from the migrations)."""
    assert ran["dropped_before_second_run"] is True
    first, second = ran["runs"][0]["exam"], ran["runs"][1]["exam"]
    assert set(first["contracts"]) == set(second["contracts"])
    assert not set(first["contracts"].values()) & set(second["contracts"].values())
    assert (first["ledger_rows"], first["jobs"]) == (second["ledger_rows"], second["jobs"])
    third = ran["runs"][2]["exam"]
    assert (third["ledger_rows"], third["jobs"]) == (first["ledger_rows"], first["jobs"])


def test_each_run_leaves_a_record_pinned_to_its_read_only_results(ran):
    """FR-39/AC-39: the immutable ``eval.run_finished`` row carries the sha256 of the results
    file, which is what ``check_pair`` later trusts; the file itself cannot be written."""
    finished = {
        a["resource_id"]: a["after"]
        for a in ran["live_audit"]
        if a["action"] == "eval.run_finished"
    }
    assert set(finished) == {r["run_id"] for r in ran["runs"]}
    for run in ran["runs"]:
        path = Path(run["results_path"])
        assert (
            finished[run["run_id"]]["results_sha256"]
            == hashlib.sha256(path.read_bytes()).hexdigest()
        )
        assert path.stat().st_mode & 0o222 == 0
        assert finished[run["run_id"]]["outcome"] == "complete"


def test_baseline_set_needs_two_complete_runs_and_never_overwrites_a_published_report(ran):
    """FR-27: the first pair becomes the baseline (report written, designation audited). A second
    designation is refused without --replace; a run is not its own pair; a name that already has a
    report is refused even with --replace (a published baseline report is a record); and --replace
    under a new name makes that pair the current baseline while the earlier report stays as it
    was, byte for byte."""
    cli = ran["cli"]
    designations = [
        a["after"] for a in ran["live_audit"] if a["action"] == "eval.baseline_designated"
    ]
    first, second = designations
    assert cli["baseline"] == "ok" and first["name"] == "iso" and first["replaced"] is None
    assert first["runs"] == [r["run_id"] for r in ran["runs"][:2]] and first["delivery"] == "batch"
    assert "already in place" in cli["second_baseline_without_replace"]
    assert "same run" in cli["a_run_is_not_its_own_pair"]
    assert cli["same_name_replace"].startswith("ReportRefused")
    assert "already exists" in cli["same_name_replace"]
    assert cli["replace_with_a_new_name"] == "ok"
    assert second["name"] == "iso3" and second["replaced"] == {"name": "iso", "runs": first["runs"]}
    assert ran["published_report_unchanged"] is True
    assert {"baseline-iso.json", "baseline-iso.md", "baseline-iso3.json"} <= set(ran["reports"])
    report = json.loads(ran["baseline_report"])
    assert report["kind"] == "baseline" and report["delivery"] == "batch"
    # The public report is numbers only: no owner file name, no text of the owner's contract.
    assert (
        OWNER_FILES[11] not in ran["baseline_report"] and "Northwind" not in ran["baseline_report"]
    )


def test_compare_takes_its_target_from_the_runs_never_from_the_command_line(ran):
    """FR-28: ``compare`` has no --target; the area is the one the change's two runs recorded
    before they ran. Had it come from anywhere else the verdict could be picked after seeing which
    measure moved. Here both runs recorded ``qa``, so the verdict names it and gives a verdict
    (a fail: nothing improved, which is the honest answer for identical runs); runs that recorded
    no target get no verdict, said plainly."""
    assert ran["cli"]["compare"] == "ok"
    reports = ran["compare_reports"]
    change = json.loads(next(r for n, r in reports.items() if n.startswith("compare-iso-change-")))
    verdict = change["comparison"]
    assert verdict["target_area"] == "qa" and verdict["main_measure"] == "qa_evidence_found"
    assert verdict["verdict"] == "fail" and verdict["failed_measures"] == ["qa_evidence_found"]
    assert verdict["baseline"] == "iso" and verdict["change"] == "iso-change"
    assert [r["run_id"][:8] for r in change["runs"]] == [r["run_id"][:8] for r in ran["runs"][2:]]
    # Cost and time sit beside the scores and never decide.
    assert {m["key"] for m in verdict["cost_time"]} >= {"cost_usd", "wall_time_s"}

    assert ran["cli"]["compare_without_a_recorded_target"] == "ok"
    untargeted = json.loads(next(r for n, r in reports.items() if n.startswith("compare-unnamed-")))
    assert untargeted["comparison"]["verdict"] == "no_verdict"
    assert untargeted["comparison"]["no_verdict_reason"] == "target not recorded before runs"


def test_compare_refuses_what_cannot_be_judged(ran):
    """FR-27/FR-28: two runs made for different changes, a baseline that does not exist, and a run
    that does not exist are refused with a reason, and none of them writes a report."""
    cli = ran["cli"]
    assert "different changes" in cli["compare_for_two_different_changes"]
    assert "no baseline is named 'nope'" in cli["compare_to_an_unknown_baseline"]
    assert "no exam run matches 'ffffffff'" in cli["compare_an_unknown_run"]
    assert len(ran["compare_reports"]) == 2


# =============================================================================================
# The command line: what must be refused, and how
# =============================================================================================


def cli_run(cli_engine, *argv: str, **env: str) -> subprocess.CompletedProcess:
    """``python -m eval.exam <argv>`` in a fresh process with no terminal (stdin is closed)."""
    return subprocess.run(
        [sys.executable, "-m", "eval.exam", *argv],
        cwd=BACKEND,
        env=process_env(cli_engine, **env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def live_audit_count(cli_engine) -> int:
    with cli_engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM audit_log")).scalar()


@pytest.mark.parametrize("command", ["run --mode sample", "prefill", "draft-routing"])
def test_a_paid_command_without_its_own_key_is_refused_before_anything_happens(cli_engine, command):
    """FR-34/AC-34: no EVAL_CLAUDE_API_KEY means no paid command, not a run on the key the app
    uses. The refusal is exit code 2 with the reason printed, and nothing is written, audited or
    created (no exam database appears)."""
    before = databases()
    proc = cli_run(cli_engine, *command.split(), "--owner", OWNER_EMAIL)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "EVAL_CLAUDE_API_KEY" in proc.stderr
    assert live_audit_count(cli_engine) == 0
    assert databases() == before


def test_a_paid_command_on_the_apps_own_key_is_refused(cli_engine):
    """FR-34: the exam's key must differ from the key used for real work, or its spend could not be
    told apart or capped apart. The refusal comes before any provider is built."""
    proc = cli_run(
        cli_engine,
        "run",
        "--mode",
        "sample",
        "--owner",
        OWNER_EMAIL,
        CLAUDE_API_KEY="one-shared-key",
        EVAL_CLAUDE_API_KEY="one-shared-key",
    )
    assert proc.returncode == 2 and "same as the app's Claude key" in proc.stderr
    assert live_audit_count(cli_engine) == 0


@pytest.mark.parametrize(
    "argv",
    [
        ("owner-import", "--from", "/nonexistent"),
        ("load-facts", "sheet.xlsx"),
        ("load-routing", "sheet.xlsx"),
        ("sheet", "facts"),
        ("ocr",),
        ("baseline", "set", "x", "a", "b"),
    ],
)
def test_a_command_that_needs_the_owner_refuses_without_a_terminal(cli_engine, argv):
    """FR-31: the owner's password and go-ahead are typed at a keyboard. With no terminal (a piped
    or scripted run) the command refuses before it reads, writes or audits anything."""
    proc = cli_run(cli_engine, *argv, "--owner", OWNER_EMAIL)
    assert proc.returncode == 2 and "needs a terminal" in proc.stderr
    assert live_audit_count(cli_engine) == 0


def test_importing_the_command_line_never_builds_the_apps_database_engine(cli_engine):
    """The whole design rests on one ordering: ``isolate()`` runs before anything imports
    ``app.core.database``, which builds its engine from the live ``DATABASE_URL`` the moment it is
    imported. If a top-level import in the command-line module (or the modules it pulls in) ever
    reached it, every command would start already connected to the live database. Checked in a
    fresh interpreter, where nothing else has imported it."""
    probe = (
        "import sys; import eval.exam.__main__ as m; m._parser(); "
        "sys.exit(7 if 'app.core.database' in sys.modules else 0)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=BACKEND,
        env=process_env(cli_engine),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_status_and_compare_work_without_the_owner_and_compare_needs_a_baseline(cli_engine):
    """FR-27/FR-28: reading where the exam stands, and judging a change, need no password; a
    comparison with no baseline designated is refused, not guessed."""
    status = cli_run(cli_engine, "status")
    assert status.returncode == 0, status.stderr
    assert "Exam content:" in status.stdout and "Baseline: none yet" in status.stdout
    compare = cli_run(cli_engine, "compare", "run-a", "run-b")
    assert compare.returncode == 2 and "no baseline yet" in compare.stderr
    assert live_audit_count(cli_engine) == 0


def test_status_lists_a_run_waiting_on_a_batch_and_one_that_never_finished(
    tmp_path, monkeypatch, capsys
):
    """FR-40: ``status`` is where the owner learns that a paid run is still out at the provider
    (so they ``--resume`` or ``--abandon`` it instead of paying for a second one) or died
    unfinished. It reads the runner's own bookkeeping; if a rename there made it stop listing
    these, the owner would see a clean status with money outstanding. Real audit rows and a real
    state file, no mocks of the runner."""
    from eval.exam import content, env, runner

    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path)

    def not_built():
        raise ValueError("not built")

    monkeypatch.setattr(content, "load_content", not_built)
    waiting, dead = uuid.uuid4().hex, uuid.uuid4().hex
    (tmp_path / waiting).mkdir()
    (tmp_path / waiting / "state.json").write_text(json.dumps({"step": "submitted"}))
    with scratch_database() as engine:
        live = make_live(engine)
        for run_id in (waiting, dead):
            env.audit(
                live,
                action="eval.spend_approved",
                actor=None,
                resource_type="eval_run",
                resource_id=run_id,
                after={"kind": "exam"},
            )
        cli._status(cli._parser().parse_args(["status"]), live, None)
    out = capsys.readouterr().out
    assert f"{waiting}  exam  WAITING ON A BATCH" in out
    assert f"{dead}  exam  started but not finished" in out


def test_a_baseline_whose_designation_cannot_be_recorded_leaves_no_report_behind(
    tmp_path, monkeypatch
):
    """FR-27: the report is written before the designation is audited (a refused report must not
    leave a baseline with nothing behind it). If the audit write then fails, no baseline exists,
    so its report must not: left in ``reports/`` it would block the name on a retry and could be
    committed as if it were a published baseline."""
    from eval.exam import content, report

    def write(obj, *, stem):
        for suffix in (".md", ".json"):
            (tmp_path / f"{stem}{suffix}").write_text("report")
        return tmp_path / f"{stem}.md"

    def cannot_record(*args, **kwargs):
        raise RuntimeError("the live database went away")

    for name, replacement in {
        "_run_id": lambda live, text: text,
        "_finished": lambda live, run_id: {"run_id": run_id, "delivery": "batch"},
        "_refuse_unfit": lambda records, what: None,
        "_baselines": lambda live: [],
        "_pair": lambda content, records: {},
    }.items():
        monkeypatch.setattr(cli, name, replacement)
    monkeypatch.setattr(content, "load_content", lambda: None)
    monkeypatch.setattr(report, "build_report", lambda *a, **kw: {})
    monkeypatch.setattr(report, "write_public_report", write)
    monkeypatch.setattr(cli.env, "audit", cannot_record)
    args = cli._parser().parse_args(["baseline", "set", "iso", "a", "b", "--owner", "o@x"])
    with pytest.raises(RuntimeError, match="went away"):
        cli._baseline_set(args, None, None)
    assert list(tmp_path.iterdir()) == []


# --- the command table itself (in this process: nothing here imports the database) ---------


def commands() -> dict[str, object]:
    parser = cli._parser()
    (sub,) = [a for a in parser._actions if a.dest == "command"]
    return dict(sub.choices)


def test_every_command_is_in_the_gate_table_and_the_table_names_no_other():
    """A command added to the parser but not to the gate table would run with no isolation
    decision made for it (KeyError at best); one in the table with no parser entry is dead."""
    assert set(commands()) == set(cli._COMMANDS) == set(cli._HANDLERS)


def test_only_the_commands_that_make_claude_calls_need_the_exam_key_and_all_that_spend_need_the_owner():
    """FR-31/FR-34: prefill, draft-routing and run call Claude (the dedicated key, no mocks, no
    third-party providers); ocr pays Reducto, not Anthropic; everything that spends, sends a
    client contract anywhere, or changes the answer keys or the baseline is owner-only. Only
    build, status and compare (numbers and public content) ask for no one."""
    paid = {c for c, (_, p) in cli._COMMANDS.items() if p}
    owner_only = {c for c, (o, _) in cli._COMMANDS.items() if o}
    assert paid == {"prefill", "draft-routing", "run"}
    assert owner_only == set(cli._COMMANDS) - {"build", "status", "compare"}
    for name, sub in commands().items():
        if name == "baseline":  # its --owner belongs to the `set` action
            (actions,) = [a for a in sub._actions if a.dest == "action"]
            sub = actions.choices["set"]
        assert ("--owner" in sub.format_help()) == cli._COMMANDS[name][0], name


def test_a_run_is_a_new_run_a_resume_or_an_abandon_and_nothing_else():
    """FR-30/FR-31: ``run`` takes exactly one of --mode, --resume, --abandon; a target is one of
    the five areas; --change and --target without a mode (a resume) are refused by the parser
    before isolation, and a comparison cannot be given a target at all."""
    parse = cli._parser().parse_args
    assert parse(["run", "--owner", "o@x", "--mode", "full"]).delivery == "batch"
    assert (
        parse(["run", "--owner", "o@x", "--mode", "full", "--delivery", "sync"]).delivery == "sync"
    )
    for bad in (
        ["run", "--owner", "o@x"],
        ["run", "--owner", "o@x", "--mode", "full", "--resume", "abc"],
        ["run", "--owner", "o@x", "--mode", "everything"],
        ["run", "--owner", "o@x", "--mode", "full", "--target", "cost"],
        ["compare", "a", "b", "--target", "qa"],
        ["baseline", "set", "only-a-name", "--owner", "o@x"],
    ):
        with pytest.raises(SystemExit):
            parse(bad)
    from eval.exam import score

    assert {t.value for t in Task} == set(score.MAIN_MEASURE)  # every target has a main measure
    with pytest.raises(SystemExit):
        cli.main(["run", "--owner", "o@x", "--resume", "abcdefgh", "--target", "qa"])


def test_no_command_can_overwrite_a_published_report():
    """FR-27: baseline reports are records. The only 'replace' on the command line renames which
    baseline is current (the earlier report stays); nothing takes a flag that rewrites a file, and
    the writer has no overwrite argument."""
    import inspect

    from eval.exam import report

    assert "replace" not in inspect.signature(report.write_public_report).parameters
    flags = {
        flag
        for sub in commands().values()
        for action in sub._actions
        for flag in action.option_strings
        if "replace" in flag or "overwrite" in flag or "force" in flag
    }
    assert flags <= {"--replace"}
    baseline = commands()["baseline"]
    (actions,) = [a for a in baseline._actions if a.dest == "action"]
    assert "--replace" in actions.choices["set"].format_help()
    assert "never overwritten" in " ".join(actions.choices["set"].format_help().split())


def test_build_is_one_command_that_builds_both_halves_and_either_alone(monkeypatch):
    """The owner runs one ``build``: the public sets and the constructed sets together, with one
    report (``run_constructed(with_public=True)`` writes every content file and prints both
    halves' counts). Without that, a public-only refresh would drop the constructed rows from the
    files they share."""
    from eval.exam import build

    calls: list = []
    monkeypatch.setattr(build, "run_public", lambda: calls.append("public"))
    monkeypatch.setattr(build, "run_constructed", lambda **kw: calls.append(("constructed", kw)))
    parse = cli._parser().parse_args
    for argv in (["build"], ["build", "--only", "public"], ["build", "--only", "constructed"]):
        cli._build(parse(argv), None, None)
    assert calls == [
        ("constructed", {"with_public": True}),
        "public",
        ("constructed", {"with_public": False}),
    ]


def test_a_refusal_exits_2_with_its_reason_an_interrupt_exits_1_and_success_exits_0(
    monkeypatch, capsys
):
    """The exit codes the plan promises (0 done, 2 refused with the reason printed, 1 stopped by
    the owner): a script or the owner can tell "nothing was spent, here is why" from a crash."""
    monkeypatch.setattr(cli, "_dispatch", lambda args: None)
    assert cli.main(["status"]) == 0

    def refuse(args):
        raise ExamRefused("not today")

    monkeypatch.setattr(cli, "_dispatch", refuse)
    assert cli.main(["status"]) == 2
    assert "not today" in capsys.readouterr().err

    def interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_dispatch", interrupt)
    assert cli.main(["status"]) == 1
    assert "closed as incomplete" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["two words", "../escape", "_lead", "a/b", "x" * 80])
def test_a_name_that_cannot_be_a_report_name_is_refused_before_anything_else_happens(name, capsys):
    """A change's or baseline's name ends up in a report's file name. Found out after two paid
    runs, a bad name would leave the owner with results and nowhere to write the verdict, so it is
    refused by the argument parser: before the process is isolated, before the password is asked,
    before any estimate."""
    for argv in (
        ["run", "--owner", "o@x", "--mode", "full", "--change", name, "--target", "qa"],
        ["baseline", "set", name, "a", "b", "--owner", "o@x"],
    ):
        with pytest.raises(SystemExit) as stopped:
            cli.main(argv)
        assert stopped.value.code == 2
        assert "letters, digits" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags",
    [["--change", "x"], ["--target", "qa"]],
)
def test_a_change_and_its_target_are_given_together_on_a_new_run(flags, capsys):
    """FR-28: the area a change is meant to improve is written down BEFORE the runs (that is what
    stops a verdict being chosen afterwards), so one without the other is refused up front."""
    with pytest.raises(SystemExit):
        cli.main(["run", "--owner", "o@x", "--mode", "full", *flags])
    assert "go together" in capsys.readouterr().err


def test_the_operator_guide_covers_every_command_and_the_three_private_settings():
    """The owner is not a developer: the guide is how they learn the steps. A command added without
    a line in it, or a setting renamed in the code but not in the guide, leaves them stuck."""
    guide = (BACKEND / "eval" / "exam" / "README.md").read_text(encoding="utf-8")
    for name in cli._COMMANDS:
        assert f"`{name}" in guide, name
    for setting in ("EVAL_CLAUDE_API_KEY", "EVAL_OWNER_EMAIL", "REDUCTO_USD_PER_CREDIT"):
        assert setting in guide
    for step in ("owner-import", "ocr", "prefill", "load-facts", "draft-routing", "load-routing"):
        assert guide.index(f"`{step}") < guide.index("`run --mode sample"), step


def test_nothing_in_the_app_a_schedule_or_ci_can_start_the_exam():
    """FR-31/AC-31: the exam only ever runs because its owner typed a command. Nothing under
    ``app/`` imports it (so no route, task or startup hook can reach it), no Celery beat entry
    names it, and no CI workflow calls it (the old ``eval.yml`` is manual and runs the old
    harness)."""
    importers = [
        str(path.relative_to(BACKEND))
        for path in (BACKEND / "app").rglob("*.py")
        if re.search(r"^\s*(from|import)\s+eval\b", path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert importers == []
    from app.jobs.celery_app import celery_app

    assert not [k for k, entry in celery_app.conf.beat_schedule.items() if "exam" in f"{k} {entry}"]
    workflows = (
        BACKEND.parent / ".github" / "workflows"
    )  # not in the dev container, which mounts backend/ only
    if workflows.is_dir():
        for path in workflows.glob("*.y*ml"):
            assert not re.search(r"eval[./]exam|-m eval\b", path.read_text(encoding="utf-8")), (
                path.name
            )
