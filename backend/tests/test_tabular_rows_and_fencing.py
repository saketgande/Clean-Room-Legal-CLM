"""PERF-02 / JOB-05: a tabular review makes one AI call per contract row (not one
per cell), and a superseded job can never overwrite a newer job's results."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (register every mapper)
from app.ai.controller import _collect_citations
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS
from app.ai.registry import skill_registry
from app.ai.schemas import TabularRowOutput
from app.core.database import utcnow
from app.core.enums import JobStatus, TabularCellStatus
from app.integrations._claude_mock import structured_payload_by_tool
from app.jobs import tasks
from app.jobs.models import JobRun
from app.tabular_review import service
from app.tabular_review.models import TabularReviewCell, TabularReviewColumn


def _cell(cell_id, column_id, contract_id="k-1", status=TabularCellStatus.PENDING):
    return SimpleNamespace(
        id=cell_id, column_id=column_id, contract_id=contract_id, status=status, answer=None, reasoning=None,
        confidence=None, citations=None, raw_ai_output=None, error_message=None, updated_by_user_id=None,
    )


def _columns(*ids):
    return [SimpleNamespace(id=column_id, prompt=f"Question for {column_id}?") for column_id in ids]


def _row_job(cell_ids):
    return SimpleNamespace(
        id="job-1", org_id="org-1", job_type="tabular_row_extraction", created_by_user_id="u-1",
        created_at=utcnow(), status=JobStatus.RUNNING, progress=5, finished_at=None, error_message=None,
        error_stack=None, metadata_json={"cell_ids": cell_ids, "tabular_review_id": "rev-1", "contract_id": "k-1"},
    )


class FakeDB:
    """Answers each query by the entity it selects and keeps the compiled SQL."""

    def __init__(self, cells, columns, newer_jobs=()):
        self.rows = {TabularReviewCell: cells, TabularReviewColumn: columns, JobRun: list(newer_jobs)}
        self.statements = []

    def scalars(self, stmt):
        self.statements.append(str(stmt.compile(dialect=postgresql.dialect())))
        rows = list(self.rows[stmt.column_descriptions[0]["entity"]])
        return SimpleNamespace(all=lambda: rows)

    def get(self, _model, _key):
        return None  # the review itself isn't needed here

    def commit(self):
        pass

    def rollback(self):
        pass


def _dispatch(monkeypatch, cells):
    created = []
    monkeypatch.setattr(service, "create_job", lambda db, **kw: created.append(kw) or SimpleNamespace(id=len(created)))
    monkeypatch.setattr(service, "dispatch_job", lambda db, *, job: job)
    db = SimpleNamespace(flush=lambda: None, commit=lambda: None, get=lambda _model, key: SimpleNamespace(id=key))
    service.dispatch_cells(db, user=SimpleNamespace(id="u-1", org_id="org-1"), review=SimpleNamespace(id="rev-1"), cells=cells)
    return created


def test_a_50_by_12_review_is_50_jobs_not_600(monkeypatch):
    cells = [_cell(f"c-{k}-{col}", f"col-{col}", contract_id=f"k-{k}") for k in range(50) for col in range(12)]
    created = _dispatch(monkeypatch, cells)
    assert len(created) == 50
    assert {job["job_type"] for job in created} == {"tabular_row_extraction"}
    assert created[0]["metadata"]["cell_ids"] == [f"c-0-{col}" for col in range(12)]


def test_wide_rows_split_and_a_lone_cell_keeps_its_own_job(monkeypatch):
    cells = [_cell(f"c-{col}", f"col-{col}") for col in range(25)] + [_cell("solo", "col-0", contract_id="k-2")]
    created = _dispatch(monkeypatch, cells)
    assert [job["job_type"] for job in created] == ["tabular_row_extraction", "tabular_row_extraction", "tabular_cell_extraction"]
    assert [len(job["metadata"].get("cell_ids") or [job["metadata"]["cell_id"]]) for job in created] == [20, 5, 1]


def test_a_row_job_asks_every_column_once_and_records_each_cells_outcome(monkeypatch):
    cells = [_cell("c-a", "col-a"), _cell("c-b", "col-b"), _cell("c-c", "col-c")]
    calls = []

    async def fake_skill(db, *, job, skill_name, input_payload):
        calls.append((skill_name, input_payload))
        return TabularRowOutput.model_validate({"answers": [
            {"column_id": "col-a", "answer": "30 days", "confidence": "high", "citations": [{"quote": "thirty (30) days"}]},
            {"column_id": "col-b", "not_found": True},
        ]})  # col-c is missing from the reply

    monkeypatch.setattr(tasks.ai_controller, "run_job_skill", fake_skill)
    job = _row_job(["c-a", "c-b", "c-c"])
    asyncio.run(tasks._run_tabular_job(FakeDB(cells, _columns("col-a", "col-b", "col-c")), job))

    [(skill, payload)] = calls
    assert skill == "tabular_row_extraction"
    assert [q["column_id"] for q in payload["questions"]] == ["col-a", "col-b", "col-c"]
    a, b, c = cells
    assert (a.status, a.answer) == (TabularCellStatus.COMPLETE, "30 days")
    assert b.status == TabularCellStatus.COMPLETE
    assert c.status == TabularCellStatus.FAILED and "no answer" in c.error_message
    assert job.status == JobStatus.SUCCEEDED


def test_a_superseded_job_never_overwrites_the_newer_run(monkeypatch):
    cells = [_cell("c-a", "col-a"), _cell("c-b", "col-b")]
    db = FakeDB(cells, _columns("col-a", "col-b"))

    async def fake_skill(_db, *, job, skill_name, input_payload):
        # While this call is in flight, someone re-runs cell B.
        db.rows[JobRun] = [SimpleNamespace(metadata_json={"cell_id": "c-b"})]
        cells[1].status = TabularCellStatus.PENDING
        return TabularRowOutput.model_validate({"answers": [
            {"column_id": "col-a", "answer": "A", "citations": [{"quote": "a"}]},
            {"column_id": "col-b", "answer": "stale B", "citations": [{"quote": "b"}]},
        ]})

    monkeypatch.setattr(tasks.ai_controller, "run_job_skill", fake_skill)
    asyncio.run(tasks._run_tabular_job(db, _row_job(["c-a", "c-b"])))

    assert cells[0].answer == "A"
    assert (cells[1].answer, cells[1].status) == (None, TabularCellStatus.PENDING)
    cell_lock = next(sql for sql in db.statements if "FROM tabular_review_cell" in sql)
    fence = next(sql for sql in db.statements if "FROM job_run" in sql)
    assert "FOR UPDATE" in cell_lock
    assert "job_run.created_at > " in fence and "job_run.resource_id IN" in fence


def test_a_job_whose_cells_were_all_re_run_makes_no_ai_call(monkeypatch):
    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("a superseded job must not call the AI")

    monkeypatch.setattr(tasks.ai_controller, "run_job_skill", must_not_run)
    cells = [_cell("c-a", "col-a")]
    job = _row_job(["c-a"])
    asyncio.run(tasks._run_tabular_job(FakeDB(cells, [], [SimpleNamespace(metadata_json={"cell_ids": ["c-a"]})]), job))
    assert job.status == JobStatus.SUCCEEDED
    assert cells[0].status == TabularCellStatus.PENDING


def test_a_failed_row_call_fails_its_cells_with_a_readable_reason(monkeypatch):
    async def boom(*_args, **_kwargs):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(tasks.ai_controller, "run_job_skill", boom)
    cells = [_cell("c-a", "col-a"), _cell("c-b", "col-b")]
    with pytest.raises(TimeoutError):
        asyncio.run(tasks._run_tabular_job(FakeDB(cells, _columns("col-a", "col-b")), _row_job(["c-a", "c-b"])))
    assert all(cell.status == TabularCellStatus.FAILED and cell.error_message for cell in cells)


def test_row_skill_is_registered_with_prompt_mock_and_citation_checks():
    spec = skill_registry.get("tabular_row_extraction")
    assert spec.output_model is TabularRowOutput and spec.requires_citations
    assert "tabular_row_extraction" in DEFAULT_SKILL_PROMPTS
    assert spec.return_tool_name in structured_payload_by_tool()
    row = TabularRowOutput.model_validate({"answers": [{"column_id": "col-a", "answer": "x", "citations": [{"quote": "q1"}]}]})
    assert [c.quote for c in _collect_citations(row)] == ["q1"]
