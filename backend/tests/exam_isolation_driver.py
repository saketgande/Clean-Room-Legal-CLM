"""Subprocess driver for ``tests/test_exam_isolation.py`` (not collected by pytest).

Process-level isolation can only be tested in a fresh interpreter: the guarantee is that the
exam's process starts on the throwaway database, so a test that runs inside pytest (where the
app's engine already exists and points at the development database) can never see it. This file
is that fresh interpreter. It starts the way ``python -m eval.exam`` does (``isolate`` first, the
rest imported after it), then does what ``run`` and ``baseline set`` / ``compare`` do, against a
small exam made here and a fake Anthropic client, and prints what it observed as one
``ISOLATION_RESULT {json}`` line for the test to judge. (The command line's own entry point cannot
be used here: it would ask for a terminal and a password. The tests in ``test_exam_isolation.py``
start it for real for the refusals; the handlers it dispatches to are called directly here.)

The documents, playbook and answer keys come from the builders in ``tests/test_exam_batch.py``, so
the two exams stay the same shape; only the databases and the process around them differ.

What is REAL: ``env.isolate`` (the process is moved onto the exam database before the app's
database module is imported), ``authenticate_owner`` against a live database the test built, the
rebuild of the exam database (``DROP`` / ``CREATE DATABASE`` and the app's own migrations), the
upload pipeline's read and persist steps, every skill the run calls, Contract Brain ``ask``,
triage, the live-ledger copy and the audit trail. What is FAKE: the Anthropic client, so nothing
can be paid for, and the embedding vectors, so no model has to be installed. Any attempt to open
a network connection is recorded and refused, which is what stands behind "no e-mail, no
provider call" instead of a list of the places that could send one.

Arguments: workdir, exam database name, owner email, owner password. The live database is the one
``DATABASE_URL`` names (a scratch database the test made, holding an organisation, the owner, two
live contracts).
"""

import asyncio
import hashlib
import json
import re
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


def main(workdir: Path, exam_db: str, owner_email: str, password: str) -> dict:
    refused_connections: list[str] = []

    def refuse(self, address, *args):
        refused_connections.append(str(address))
        raise OSError("the isolation test allows no network connection")

    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse

    # --- the real start of `python -m eval.exam`: isolate first, then everything else ----------
    from eval.exam import env

    work = workdir / "work"
    env.WORK_DIR = work  # isolate() keeps the exam's stored files under it
    live = env.isolate(paid=False, eval_db_name=exam_db)

    import pytest
    from sqlalchemy import create_engine, event, func, inspect, select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    from app.ai import embeddings
    from app.auth.models import User
    from app.contract_brain.models import BrainQuery
    from app.contracts.lifecycle import ContractLifecycleService
    from app.contracts.models import Contract, ContractStageHistory
    from app.core import database
    from app.core.models import AICallLog, AuditLog
    from app.intake.models import IntakeRequest
    from app.integrations.resend import ResendClient
    from app.integrations.sendgrid import SendGridClient
    from app.jobs.models import JobRun
    from app.jobs.service import JobsService
    from app.notifications.models import Notification
    from app.workflows.models import Workflow, WorkflowRun
    from eval.exam import content as content_mod
    from eval.exam import evaldb, report, routing, runner, score
    from eval.exam import owner as owner_mod
    from eval.exam.__main__ import _baseline_set, _compare, _parser
    from eval.exam.content import (
        OWNER_FILES,
        ExamContent,
        ExamDocument,
        ExtractionItem,
        FieldKey,
        OwnerFactKey,
        OwnerManifestEntry,
        PortfolioQuestion,
        QAItem,
        ReviewItem,
        RoutingKey,
        RoutingRequest,
        SourceCategory,
    )
    from tests import exam_fake_anthropic
    from tests.test_exam_batch import (
        GOVERNING_LAW,
        PLAYBOOK,
        TERMS_B,
        doc,
        docx_of,
        rule_keys,
        sha,
        text_of,
    )
    from tests.test_exam_gate import Typist

    exam_url = make_url(database.engine.url)
    result: dict = {
        "engine_database": exam_url.database,
        "live_database": make_url(live.url).database,
        "refused_connections": refused_connections,
    }
    # Control: the network guard is only evidence if it would have caught a connection.
    try:
        socket.create_connection(("203.0.113.1", 80), timeout=1)  # a documentation-only address
    except OSError:
        pass
    result["control_connection_seen"] = len(refused_connections) == 1
    refused_connections.clear()

    # --- the live database: one workflow for routing to pick from, then a baseline of counts ---
    live_engine = live.sessions.kw["bind"]
    with live.sessions() as db:
        db.add(
            Workflow(
                org_id=live.org_id,
                name="NDA review",
                description="Reviews NDAs",
                enabled=True,
                is_builtin=False,
                eval_order=1,
                version=1,
                criteria={},
                steps=[],
            )
        )
        db.commit()
    snapshot = routing.snapshot_workflows(live)
    live_tables = sorted(inspect(live_engine).get_table_names())

    def live_counts() -> dict[str, int]:
        with live_engine.connect() as conn:
            return {
                t: conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in live_tables
            }

    result["live_counts_before"] = live_counts()
    statements: list[str] = []
    watching = [True]  # this driver's own counting and reading afterwards is not the exam's

    @event.listens_for(live_engine, "before_cursor_execute")
    def watch(conn, cursor, statement, parameters, context, executemany):
        if watching[0]:
            statements.append(statement)

    # --- the exam: a handful of documents, one item of every kind, one owner document ---------
    docs_dir = workdir / "contracts"
    (docs_dir / "public").mkdir(parents=True)
    owner_dir = workdir / "owner_files"
    owner_dir.mkdir()
    texts = {
        "public/cp-i1.txt": text_of("Cora", "Delta"),
        "public/cuad-i1.txt": text_of("Acme", "Beta"),
        "public/pf-i1.txt": text_of("Gamma", "Harbor", TERMS_B),
    }
    for name, body in texts.items():
        (docs_dir / name).write_bytes(body.encode())
    documents = [
        doc("cp-i1", "public/cp-i1.txt", texts["public/cp-i1.txt"].encode(), ai_steps=["metadata"]),
        doc(
            "cuad-i1",
            "public/cuad-i1.txt",
            texts["public/cuad-i1.txt"].encode(),
            category=SourceCategory.PUBLIC,
            dataset="cuad",
        ),
        doc(
            "pf-i1",
            "public/pf-i1.txt",
            texts["public/pf-i1.txt"].encode(),
            uploader="portfolio",
        ),
    ]
    # The owner's contract: one of the 14 allowed names, generated text, never batched.
    owner_name = OWNER_FILES[11]
    owner_bytes = docx_of(text_of("Northwind", "Orchid"))
    (owner_dir / owner_name).write_bytes(owner_bytes)
    docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    frozen = asyncio.run(
        owner_mod.read_as_app(
            owner_bytes, mime_type=docx_mime, filename=owner_name, ocr=owner_mod.FrozenOCR()
        )
    )
    entry = OwnerManifestEntry(
        doc_id="owner-12",
        name=owner_name,
        file_sha256=sha(owner_bytes),
        mime_type=docx_mime,
        pages=0,
        kind="text",
        frozen_text_sha256=sha(frozen.text.encode()),
        frozen_method=frozen.method,
        imported_at=datetime.now(UTC),
    )
    documents.append(
        ExamDocument(
            doc_id="owner-12",
            category=SourceCategory.OWNER,
            dataset=None,
            file=owner_name,
            mime_type=docx_mime,
            file_sha256=entry.file_sha256,
            title=Path(owner_name).stem,
            ai_steps=["metadata", "clauses"],
        )
    )
    workflow_id = snapshot["workflows"][0]["id"]
    content = ExamContent(
        documents=documents,
        review=[
            ReviewItem(
                item_id="rv-cp-i1",
                doc_id="cp-i1",
                category=SourceCategory.CONSTRUCTED,
                source="constructed",
                playbook="cp_msa",
                rules=rule_keys(planted=True),
            )
        ],
        extraction=[
            ExtractionItem(
                item_id="ex-cuad-i1",
                doc_id="cuad-i1",
                category=SourceCategory.PUBLIC,
                source="CUAD",
                fields=[
                    FieldKey(
                        field="governing_law",
                        kind="jurisdiction",
                        value="India",
                        quote=GOVERNING_LAW,
                    )
                ],
            )
        ],
        qa=[
            QAItem(
                item_id="qa-001",
                doc_id="cuad-i1",
                category=SourceCategory.PUBLIC,
                source="LegalBench-RAG",
                question="Which law governs the Agreement and which courts have jurisdiction?",
                answer="Indian law; the Mumbai courts.",
                passages=[GOVERNING_LAW],
            )
        ],
        portfolio=[
            PortfolioQuestion(
                item_id="pq-01",
                question="Which contracts renew automatically for one year terms?",
                fact="auto_renew",
                expected_doc_ids=["pf-i1"],
            )
        ],
        routing=[
            RoutingRequest(
                item_id="rt-01",
                channel="email",
                subject="Need an NDA",
                body="Please send us an NDA.",
                drafted_for_workflow_id=workflow_id,
                drafting_model="claude-sonnet-5-5",
            )
        ],
        owner_keys=[
            OwnerFactKey(
                doc_id="owner-12",
                field="governing_law",
                status="confirmed",
                value="India",
                quote=GOVERNING_LAW,
                char_start=None,
                char_end=None,
                suggested_value="India",
                suggested_quote=GOVERNING_LAW,
                suggested_by="claude-sonnet-5-5",
                file_sha256=entry.file_sha256,
                frozen_text_sha256=entry.frozen_text_sha256,
                sheet_sha256="s" * 64,
                loaded_at=datetime.now(UTC),
            )
        ],
        routing_keys=[
            RoutingKey(
                item_id="rt-01",
                status="confirmed",
                workflow_id=workflow_id,
                workflow_fingerprint=snapshot["fingerprint"],
                sheet_sha256="r" * 64,
                loaded_at=datetime.now(UTC),
            )
        ],
        playbooks={"cp_msa": PLAYBOOK},
        sample={},
        workflow_snapshot=snapshot,
        owner_manifest=[entry],
    )

    # Where the exam reads and writes: under the workdir, never the owner's private folders.
    runs_dir, reports_dir = workdir / "runs", workdir / "reports"
    reports_dir.mkdir()
    patches = pytest.MonkeyPatch()
    for module in (evaldb, score):
        patches.setattr(module, "DOCS_DIR", docs_dir)
    patches.setattr(evaldb, "WORK_DIR", work)
    patches.setattr(evaldb, "_OWNER_FILES_DIR", owner_dir)
    for module in (runner, report):
        patches.setattr(module, "RUNS_DIR", runs_dir)
    for module in (report, content_mod):
        patches.setattr(module, "REPORTS_DIR", reports_dir)
    patches.setattr("eval.exam.__main__.REPORTS_DIR", reports_dir)
    patches.setattr(owner_mod, "read_manifest", lambda: [entry])
    patches.setattr(owner_mod, "stale_keys", lambda keys, manifest=None: [])
    patches.setattr(content_mod, "load_content", lambda: content)
    patches.setattr(runner, "POLL_SECONDS", 0)
    patches.setattr(
        embeddings,
        "_embed_local",
        lambda items: [embeddings._deterministic_mock_vector(t) for t in items],
    )
    # The local cross-encoder downloads its model on first use. On a machine without it cached
    # that is a harmless connection the socket guard below would count as a leak, so the
    # reranker is stood in for with input order (what rerank_order falls back to anyway).
    patches.setattr(
        "app.contract_brain.rerank._local_rerank_order",
        lambda query, documents, top_n: list(range(top_n)),
    )

    # The fake provider: the real request path (hook, batches) with a dummy key and no network.
    fake = exam_fake_anthropic.install(patches)
    retrieve = fake.messages.batches.retrieve

    async def retrieve_with_a_datetime(batch_id):
        # The SDK's to_dict() leaves timestamps as datetimes, which a state file cannot hold.
        body = {**(await retrieve(batch_id)).to_dict(), "ended_at": datetime.now(UTC)}
        return SimpleNamespace(to_dict=lambda: body)

    fake.messages.batches.retrieve = retrieve_with_a_datetime

    # Everything a start could do besides read: recorded, not made to fail, because the app's own
    # best-effort blocks swallow exceptions and would hide a call from this driver.
    side_effects: list[str] = []

    def record(name):
        def spy(*args, **kwargs):
            side_effects.append(name)

        return spy

    def record_async(name):
        async def spy(*args, **kwargs):
            side_effects.append(name)

        return spy

    from celery import Celery
    from celery.app.task import Task

    import app.workflows.service as workflows_service

    patches.setattr(Celery, "send_task", record("celery.send_task"))
    patches.setattr(Task, "apply_async", record("celery.apply_async"))
    patches.setattr(JobsService, "dispatch_job", record("dispatch_job"))
    patches.setattr(
        ContractLifecycleService, "transition_contract_stage", record("transition_contract_stage")
    )
    for name in ("start_flow", "start_flow_for_contract"):
        patches.setattr(workflows_service, name, record_async(name))
    patches.setattr(ResendClient, "send_email", record_async("resend.send_email"))
    patches.setattr(SendGridClient, "send_email", record_async("sendgrid.send_email"))

    # Control: the dispatch recorder is only evidence if it would have caught a send.
    from app.jobs.celery_app import celery_app

    celery_app.send_task("isolation.control")
    result["control_dispatch_seen"] = side_effects == ["celery.send_task"]
    side_effects.clear()

    # --- the owner's gate, for real, then two full runs with the exam database dropped between --
    from eval.exam.env import authenticate_owner

    owner = authenticate_owner(live, owner_email, command="run")

    def exam_state() -> dict:
        with database.SessionLocal() as db:
            contracts = {c.title: c.id for c in db.scalars(select(Contract))}
            queries = list(db.scalars(select(BrainQuery)))
            return {
                "contracts": contracts,
                "brain_query_contract_ids": sorted(
                    {
                        c
                        for q in queries
                        for c in (q.retrieval_metadata or {}).get("contract_ids", [])
                    }
                ),
                "brain_queries": len(queries),
                "ledger_rows": db.scalar(select(func.count()).select_from(AICallLog)),
                "notifications": db.scalar(select(func.count()).select_from(Notification)),
                "workflow_runs": db.scalar(select(func.count()).select_from(WorkflowRun)),
                "stage_history": db.scalar(select(func.count()).select_from(ContractStageHistory)),
                "intake_requests": db.scalar(select(func.count()).select_from(IntakeRequest)),
                "jobs": db.scalar(select(func.count()).select_from(JobRun)),
                "users": sorted(db.scalars(select(User.email))),
            }

    server = create_engine(live.url, poolclass=NullPool, isolation_level="AUTOCOMMIT")

    def database_exists() -> bool:
        with server.connect() as conn:
            return (
                conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": exam_db}
                ).scalar()
                is not None
            )

    def run_once(**kwargs) -> dict:
        creates = len(fake.batch_creates)
        streamed = len(fake.streamed)
        results = asyncio.run(
            runner.execute_exam_run(
                live=live,
                owner=owner,
                mode="full",
                delivery="batch",
                change=kwargs.get("change"),
                target=kwargs.get("target"),
                ask=Typist("25"),
                eval_db_name=exam_db,
                content=content,
            )
        )
        sent_in_batches = json.dumps(fake.batch_creates[creates:])
        sent_directly = json.dumps(fake.streamed[streamed:])
        return {
            "run_id": results["run_id"],
            "outcome": results["outcome"],
            "delivery": results["delivery"],
            "items": {i["item_id"]: i["status"] for i in results["items"]},
            "batch_sizes": [len(b) for b in fake.batch_creates[creates:]],
            "direct_calls": len(fake.streamed) - streamed,
            "owner_text_in_batches": "Northwind" in sent_in_batches,
            "owner_text_sent_directly": "Northwind" in sent_directly,
            "results_path": str(runs_dir / results["run_id"] / "results.json"),
            "exam": exam_state(),
        }

    runs = [run_once()]
    # AC-38: drop the exam database from outside and run again; the run rebuilds it from nothing.
    with server.connect() as conn:
        conn.execute(text(f'DROP DATABASE "{exam_db}" WITH (FORCE)'))
    result["dropped_before_second_run"] = not database_exists()
    runs.append(run_once())
    # Two more, made "for a change", so the CLI's own baseline and compare run on real records.
    runs += [run_once(change="iso-change", target="qa") for _ in range(2)]
    result["runs"] = runs

    # --- the CLI's baseline and compare, on those four real records ---------------------------
    parser = _parser()
    ids = [r["run_id"] for r in runs]

    def attempt(handler, argv, who=owner) -> str:
        """One command's handler on parsed arguments: "ok", or what it refused with."""
        try:
            handler(parser.parse_args(argv), live, who)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"
        return "ok"

    def baseline(name, a, b, *flags):
        return ["baseline", "set", name, a, b, "--owner", owner_email, *flags]

    def published(name: str) -> str:
        path = reports_dir / name
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""

    cli_result = result["cli"] = {}
    cli_result["baseline"] = attempt(_baseline_set, baseline("iso", ids[0], ids[1]))
    first = published("baseline-iso.json")
    cli_result["second_baseline_without_replace"] = attempt(
        _baseline_set, baseline("iso2", ids[0], ids[1])
    )
    cli_result["a_run_is_not_its_own_pair"] = attempt(
        _baseline_set, baseline("iso4", ids[0], ids[0])
    )
    # A prefix of the id is enough (the reports show eight characters); no --target on compare.
    cli_result["compare"] = attempt(_compare, ["compare", ids[2][:8], ids[3]], None)
    cli_result["compare_without_a_recorded_target"] = attempt(
        _compare, ["compare", ids[0], ids[1]], None
    )
    cli_result["compare_for_two_different_changes"] = attempt(
        _compare, ["compare", ids[1], ids[2]], None
    )
    cli_result["compare_to_an_unknown_baseline"] = attempt(
        _compare, ["compare", ids[2], ids[3], "--baseline", "nope"], None
    )
    cli_result["compare_an_unknown_run"] = attempt(_compare, ["compare", "ffffffff", ids[3]], None)
    # Naming the same report again, even with --replace, may never overwrite what was published.
    cli_result["same_name_replace"] = attempt(
        _baseline_set, baseline("iso", ids[0], ids[1], "--replace")
    )
    cli_result["replace_with_a_new_name"] = attempt(
        _baseline_set, baseline("iso3", ids[2], ids[3], "--replace")
    )
    result["published_report_unchanged"] = published("baseline-iso.json") == first != ""
    result["reports"] = sorted(p.name for p in reports_dir.iterdir())
    result["baseline_report"] = (reports_dir / "baseline-iso.json").read_text(encoding="utf-8")
    result["compare_reports"] = {
        p.name: p.read_text(encoding="utf-8") for p in reports_dir.glob("compare-*.json")
    }

    # --- what the live database looks like now ------------------------------------------------
    watching[0] = False
    result["live_counts_after"] = live_counts()
    result["live_statement_tables"] = sorted(
        {
            name
            for statement in statements
            for name in re.findall(r'(?:from|into|update|join)\s+"?([a-z_]+)"?', statement.lower())
            if name in live_tables
        }
    )
    with live.sessions() as db:
        result["live_audit"] = [
            {"action": a.action, "resource_id": a.resource_id, "after": a.after}
            for a in db.scalars(select(AuditLog).order_by(AuditLog.created_at, AuditLog.id))
        ]
        result["live_ledger"] = [
            {
                "eval_run_id": r.eval_run_id,
                "batched": r.batched,
                "provider_request_id": r.provider_request_id,
                "has_text": any(
                    v is not None
                    for v in (r.input_payload, r.validated_output, r.raw_ai_output, r.job_id)
                ),
            }
            for r in db.scalars(select(AICallLog))
        ]
        result["live_contract_titles"] = sorted(db.scalars(select(Contract.title)))
    result["side_effects"] = side_effects
    result["fake_streamed_after"] = len(fake.streamed)
    server.dispose()
    return result


if __name__ == "__main__":
    out = main(Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4])
    print("ISOLATION_RESULT " + json.dumps(out, default=str))
