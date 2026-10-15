"""Checking a drafting template against its playbook, and the two fixes that came with it.

Failures this guards:
* a template that the playbook's own AI review would flag was never noticed until a contract
  drafted from it came back with risks (the only guard was a literal-phrase test, which cannot
  judge meaning). The check runs the same review on the template text;
* the check drifting from the real review (different context, different payload) and then
  passing templates the real review flags;
* the check storing a run, findings or a risk band for a contract that does not exist, or
  passing off a keyword scan as a review when the AI could not run;
* the review's ``rule_index`` landing on the wrong rule: the prompt numbers rules by creation
  time but the run read them unordered, so after a rule was edited a finding hung on another rule;
* the reviewer listing a clause it had itself found compliant ("no deviation to flag"), which then
  showed as a risk, a HIGH one when the rule needs approval, on paper that meets the playbook;
* the NDA request form offering a term the NDA playbook rejects;
* a rule's approval flag read by the model as 'this clause needs sign-off', which got compliant
  clauses listed (and raised to HIGH) because the rule is approval-required;
* the endpoints (spec 006): the old blocking check still being there beside the recorded one, a
  check that is not a 202 plus a job, a check by someone who may only view playbooks, a static path
  (``/options``, ``/check-all``) read as a template key, a check or file of another organisation
  being readable, and a playbook publish or library seed that fails because of the template checks
  it starts (or starts them before the publish has committed).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every mapper)
from app.ai.context import build_contract_context, text_only_context
from app.ai.models import AISkillRun
from app.ai.schemas import PlaybookDeviationOutput, PlaybookReviewOutput
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.core.deps import get_current_user, get_db
from app.core.enums import JobStatus, PlaybookStatus, UserStatus
from app.core.models import AuditLog
from app.drafting_templates import checks
from app.drafting_templates.models import (
    DraftingTemplateCheck,
    DraftingTemplateType,
    DraftingTemplateVersion,
)
from app.drafting_templates.service import SAMPLE_VALUES, TEMPLATES, default_body, render_sample
from app.intake import drafting
from app.intake.agreement_forms import form_def
from app.jobs.models import JobRun
from app.org_structure.models import OrgUnit
from app.organizations.models import Organization
from app.playbooks.models import (
    Playbook,
    PlaybookDeviation,
    PlaybookRule,
    PlaybookRun,
    PlaybookVersion,
)
from app.playbooks.routes import _rule_payloads_for_ai
from app.playbooks.service import (
    PlaybooksService,
    ai_deviations_to_evaluated,
    kept_deviations,
    rules_in_review_order,
)


@pytest.fixture
def db():
    """A session whose work is rolled back at the end, so nothing the test writes outlives it."""
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


def _org_user(db: Session, *, permissions: list[str]) -> User:
    org = Organization(id=new_uuid(), name="check", slug=f"check-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    unit = OrgUnit(org_id=org.id, name="HQ", parent_id=None)
    db.add(unit)
    role = Role(org_id=org.id, name=f"role-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    for value in permissions:
        perm = db.query(Permission).filter(Permission.value == value).one_or_none()
        if perm is None:
            perm = Permission(value=value)
            db.add(perm)
        role.permissions.append(perm)
    db.add(role)
    user = User(org_id=org.id, email=f"u-{uuid.uuid4().hex[:8]}@example.com", full_name="Checker",
                hashed_password="hash", status=UserStatus.ACTIVE)
    db.add(user)
    db.flush()
    db.add(UserRoleGrant(user_id=user.id, role_id=role.id, org_id=org.id, org_unit_id=unit.id))
    db.flush()
    return user


def _playbook(db: Session, user: User, name: str, rules: list[dict]) -> tuple[Playbook, PlaybookVersion]:
    """A published playbook with the given rules, created one after another (distinct created_at)."""
    playbook = Playbook(org_id=user.org_id, name=name, status=PlaybookStatus.PUBLISHED,
                        created_by_user_id=user.id, updated_by_user_id=user.id)
    db.add(playbook)
    db.flush()
    version = PlaybookVersion(org_id=user.org_id, playbook_id=playbook.id, version_number=1,
                              status=PlaybookStatus.PUBLISHED, created_by_user_id=user.id,
                              updated_by_user_id=user.id)
    db.add(version)
    db.flush()
    playbook.current_version_id = version.id
    base = version.created_at
    for i, r in enumerate(rules):
        db.add(PlaybookRule(org_id=user.org_id, playbook_version_id=version.id, rule_type="standard_position",
                            risk_level="medium", created_at=base + timedelta(seconds=i),
                            created_by_user_id=user.id, updated_by_user_id=user.id, **r))
    db.flush()
    return playbook, version


_RULES = [
    {"clause_type": "limitation_of_liability", "preferred_position": "Cap at twelve months of fees.",
     "rationale": "Bound our exposure."},
    {"clause_type": "payment_terms", "preferred_position": "Pay in arrears.", "rationale": "Cash flow.",
     "approval_required": True},
    {"clause_type": "termination", "preferred_position": "Either side on 30 days.", "rationale": "Exit."},
]


class _FakeReview:
    """Stands in for ai_controller.run_structured_skill and records how it was called."""

    def __init__(self, output: PlaybookReviewOutput | Exception):
        self.output = output
        self.calls: list[dict] = []

    async def __call__(self, db, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


@pytest.fixture
def api(db):
    from fastapi.testclient import TestClient

    from app.core.rate_limit import limiter
    from app.main import app

    def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    previous = limiter.enabled
    limiter.enabled = False
    try:
        with TestClient(app) as client:
            yield client
    finally:
        limiter.enabled = previous
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def _as(user: User):
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: user


def _ai(monkeypatch, fake: _FakeReview) -> _FakeReview:
    from app.ai.controller import ai_controller

    monkeypatch.setattr(ai_controller, "run_structured_skill", fake)
    return fake


SAAS = "Software / SaaS Agreement Playbook"


# --- the check reads the same context a real review reads --------------------------------------


def test_the_text_context_has_exactly_the_manifest_a_stored_contract_gives():
    """The model reads this manifest next to the text. If a key were added to the real one and not
    here, the check would be a different question from the real review."""
    contract = Contract(id="c1", org_id="org1", title="T", owner_user_id="u1", current_authoritative_version_id="v1")
    version = ContractVersion(id="v1", org_id="org1", contract_id="c1", text_snapshot_id="s1")
    snapshot = ContractTextSnapshot(id="s1", org_id="org1", contract_id="c1", text="Some text.")
    rows = {Contract: contract, ContractVersion: version, ContractTextSnapshot: snapshot}

    class _FakeDB:
        def get(self, model, obj_id):
            return rows.get(model)

    real = build_contract_context(_FakeDB(), user=None, org_id="org1", contract_id="c1",
                                  skill_name="playbook_review")
    ours = text_only_context(text="Some text.", title="T", contract_type="SaaS", counterparty_name="X")
    assert set(ours.manifest) == set(real.manifest)
    assert ours.text == real.text == "Some text."


def test_the_text_context_makes_no_database_row():
    """The Contract in it is a stand-in for the prompt. If it ever had an id the AI run row would
    point at a contract that does not exist."""
    ctx = text_only_context(text="x", title="T", contract_type=None)
    assert ctx.contract.id is None and ctx.version is None and ctx.snapshot is None


# --- the rules the model numbers are the rules the run reads -----------------------------------


def test_rule_numbers_still_name_the_same_rules_after_one_is_edited(db):
    """The prompt numbers rules by creation time. After the first rule was edited Postgres stores
    it last, and a read with no ORDER BY then returned it last: the model's 'rule 1' became rule 3."""
    user = _org_user(db, permissions=["playbook:run"])
    _, version = _playbook(db, user, SAAS, _RULES)
    first = rules_in_review_order(db, org_id=user.org_id, playbook_version_id=version.id)[0]
    first.rationale = "Edited later."
    db.flush()
    db.expire_all()
    # Which order an unordered read gives depends on the plan the database picks (an index scan keeps
    # the old order, a table scan gives storage order). Force the table scan so this test fails
    # whenever the read is not explicitly ordered, on any data size.
    db.execute(text("SET LOCAL enable_indexscan = off"))
    db.execute(text("SET LOCAL enable_bitmapscan = off"))
    ordered = rules_in_review_order(db, org_id=user.org_id, playbook_version_id=version.id)
    assert [r.clause_type for r in ordered] == ["limitation_of_liability", "payment_terms", "termination"]
    payload = PlaybooksService(db)._auto_rule_payloads(org_id=user.org_id, playbook_version_id=version.id)
    assert [p["clause_type"] for p in payload] == [r.clause_type for r in ordered]


def test_every_review_path_gives_the_model_the_same_rules_and_a_clear_approval_key(db):
    """A rule's approval flag went to the model as ``approval_required``; it read that as 'this rule
    always needs sign-off' and listed clauses that meet the rule so someone would approve them, each
    then raised to HIGH. It is sent as ``approval_needed_if_deviated`` now, by one builder: the
    contract review and the playbook-test route used two copies, which could drift apart."""
    user = _org_user(db, permissions=["playbook:run"])
    _, version = _playbook(db, user, SAAS, _RULES)

    from_service = PlaybooksService(db)._auto_rule_payloads(org_id=user.org_id, playbook_version_id=version.id)
    from_route = _rule_payloads_for_ai(db, org_id=user.org_id, playbook_version_id=version.id)

    assert from_service == from_route
    assert [p["rule_index"] for p in from_service] == [1, 2, 3]
    assert all("approval_required" not in p for p in from_service)
    assert [p["approval_needed_if_deviated"] for p in from_service] == [False, True, False]  # _RULES[1] needs approval


def test_the_review_prompt_explains_the_approval_key_the_rules_carry():
    """The key only helps if the prompt says what it means: if it is dropped from one, the model
    is back to guessing, and the other side of the rename is silent."""
    from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS

    prompt = DEFAULT_SKILL_PROMPTS["playbook_review"]
    assert "approval_needed_if_deviated" in prompt
    assert "no sign-off and no item" in prompt


def test_a_new_playbook_version_numbers_its_rules_as_the_source_did(db):
    """Cloning read the source rules unordered, so after an edit the new version could list them in a
    different order, and 'rule 3' in the next review meant another rule."""
    user = _org_user(db, permissions=["playbook:update"])
    playbook, version = _playbook(db, user, SAAS, _RULES)
    first = rules_in_review_order(db, org_id=user.org_id, playbook_version_id=version.id)[0]
    first.rationale = "Edited later."
    db.flush()
    db.expire_all()
    db.execute(text("SET LOCAL enable_indexscan = off"))
    db.execute(text("SET LOCAL enable_bitmapscan = off"))

    new = PlaybooksService(db).clone_playbook_version(playbook=playbook, user=user, source_version=version, summary="v2")
    db.flush()

    cloned = rules_in_review_order(db, org_id=user.org_id, playbook_version_id=new.id)
    assert [r.clause_type for r in cloned] == ["limitation_of_liability", "payment_terms", "termination"]


# --- review_text: same call as a contract review, nothing stored -------------------------------


def test_review_text_asks_the_playbook_review_the_question_a_contract_review_asks(db, monkeypatch):
    user = _org_user(db, permissions=["playbook:run"])
    playbook, version = _playbook(db, user, SAAS, _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(summary="ok")))

    import asyncio

    result = asyncio.run(PlaybooksService(db).review_text(
        user=user, playbook=playbook, text="The whole template.", title="Template",
        contract_type="Software / SaaS Agreement", counterparty_name="Globex",
        resource_type="drafting_template", resource_id="saas"))

    (call,) = fake.calls
    assert call["skill_name"] == "playbook_review"
    assert call["created_by_user_id"] == user.id and call["org_id"] == user.org_id
    # The payload of auto_review_contract without the two ids that name a stored contract.
    assert set(call["input_payload"]) == {"playbook_id", "playbook_version_id", "rules"}
    assert call["input_payload"]["playbook_version_id"] == version.id
    assert call["input_payload"]["rules"] == PlaybooksService(db)._auto_rule_payloads(
        org_id=user.org_id, playbook_version_id=version.id)
    assert call["contract_context"].text == "The whole template."
    assert call["resource_type"] == "drafting_template" and call["resource_id"] == "saas"
    assert result["findings"] == [] and result["rule_count"] == 3 and result["summary"] == "ok"


def test_review_text_reports_each_finding_against_the_rule_the_model_numbered(db, monkeypatch):
    user = _org_user(db, permissions=["playbook:run"])
    playbook, _ = _playbook(db, user, SAAS, _RULES)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(deviations=[
        PlaybookDeviationOutput(rule_index=2, clause_type="payment_terms", severity="low",
                                issue="Invoices in advance.", original_text="invoice annually in advance",
                                suggested_fix="Invoice in arrears."),
        PlaybookDeviationOutput(rule_index=None, clause_type="other", severity="medium", issue="Odd clause."),
    ])))
    import asyncio

    result = asyncio.run(PlaybooksService(db).review_text(
        user=user, playbook=playbook, text="t", title="T", contract_type=None, counterparty_name=None,
        resource_type="drafting_template", resource_id="saas"))
    first, second = result["findings"]
    assert first["clause_type"] == "payment_terms" and first["rule_index"] == 2
    assert first["quote"] == "invoice annually in advance" and first["suggested_fix"] == "Invoice in arrears."
    assert first["approval_required"] is True  # taken from rule 2, which needs approval
    assert second["rule_index"] is None and second["approval_required"] is False


# --- an item the model finds compliant is not a risk -------------------------------------------


def _compliant_item(rule_index: int, clause: str) -> PlaybookDeviationOutput:
    return PlaybookDeviationOutput(
        rule_index=rule_index, clause_type=clause, severity="low", position="preferred",
        issue="On a plain reading this meets the preferred position. No deviation to flag.",
    )


def test_an_item_the_model_marks_as_not_a_deviation_is_discarded(db):
    """The reviewer listed rule 2 and in writing the issue found it compliant. It was still saved,
    and because rule 2 needs approval its severity was raised to HIGH: a risk on a clause that
    meets the playbook, shown on a contract drafted from our own template."""
    user = _org_user(db, permissions=["playbook:run"])
    _, version = _playbook(db, user, SAAS, _RULES)
    rules = rules_in_review_order(db, org_id=user.org_id, playbook_version_id=version.id)
    output = PlaybookReviewOutput(deviations=[
        PlaybookDeviationOutput(rule_index=1, clause_type="limitation_of_liability", severity="high",
                                issue="Cap is unlimited."),
        _compliant_item(2, "payment_terms"),
    ])

    evaluated = ai_deviations_to_evaluated(ai_output=output, rules=rules)

    assert [e.clause_type for e in evaluated] == ["limitation_of_liability"]
    assert [d.clause_type for d in kept_deviations(output)] == ["limitation_of_liability"]


def test_a_review_payload_without_a_position_keeps_every_item():
    """Stored and mocked review outputs have no position. They must keep meaning 'a deviation', or
    every older payload would silently turn into 'no risks'."""
    output = PlaybookReviewOutput.model_validate({"deviations": [
        {"rule_index": 1, "clause_type": "payment_terms", "severity": "high", "issue": "Pays in advance."},
    ]})
    assert output.deviations[0].position == "outside"
    assert len(kept_deviations(output)) == 1 and kept_deviations(None) == []


def test_an_item_at_the_fallback_position_is_still_a_tracked_deviation():
    """Only 'preferred' is discarded. A clause at the fallback is the accepted-but-tracked case the
    playbook exists to surface."""
    output = PlaybookReviewOutput(deviations=[
        PlaybookDeviationOutput(rule_index=1, clause_type="term", issue="At the fallback.", position="fallback"),
        PlaybookDeviationOutput(rule_index=2, clause_type="cap", issue="Worse than the fallback.", position="outside"),
    ])
    assert [d.clause_type for d in kept_deviations(output)] == ["term", "cap"]


def test_the_model_is_told_it_must_answer_position_for_every_item():
    """A field with a default is optional in the schema, and the model leaves optional fields out of a
    tool call. The schema the model gets must list position as required, say what each answer means,
    and ask for it right after the issue it concludes."""
    item = PlaybookReviewOutput.model_json_schema()["$defs"]["PlaybookDeviationOutput"]
    assert "position" in item["required"]
    assert item["properties"]["position"]["enum"] == ["preferred", "fallback", "outside"]
    assert "NOT a deviation" in item["properties"]["position"]["description"]
    props = list(item["properties"])
    assert props.index("position") == props.index("issue") + 1


def test_review_text_leaves_out_a_discarded_item_and_the_summary_that_may_describe_it(db, monkeypatch):
    """review_text pairs the model's items with the evaluated ones one by one. A discarded item has no
    evaluated twin, so that pairing has to use the same kept list. The model's summary was written
    about everything it listed ('one deviation is flagged: ...'), so it is not passed on."""
    user = _org_user(db, permissions=["playbook:run"])
    playbook, _ = _playbook(db, user, SAAS, _RULES)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(
        summary="One deviation is flagged: payment terms.",
        deviations=[_compliant_item(2, "payment_terms")],
    )))
    import asyncio

    result = asyncio.run(PlaybooksService(db).review_text(
        user=user, playbook=playbook, text="t", title="T", contract_type=None, counterparty_name=None,
        resource_type="drafting_template", resource_id="saas"))

    assert result["findings"] == [] and result["summary"] is None


def test_review_text_keeps_the_summary_when_nothing_was_discarded(db, monkeypatch):
    user = _org_user(db, permissions=["playbook:run"])
    playbook, _ = _playbook(db, user, SAAS, _RULES)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(
        summary="One deviation is flagged: payment terms.",
        deviations=[PlaybookDeviationOutput(rule_index=2, clause_type="payment_terms", severity="low",
                                            issue="Invoices in advance.")],
    )))
    import asyncio

    result = asyncio.run(PlaybooksService(db).review_text(
        user=user, playbook=playbook, text="t", title="T", contract_type=None, counterparty_name=None,
        resource_type="drafting_template", resource_id="saas"))

    assert len(result["findings"]) == 1 and result["summary"] == "One deviation is flagged: payment terms."


# --- the endpoints ------------------------------------------------------------------------------

API = "/api/v1/drafting-templates"
READ_RUN = ["playbook:read", "playbook:run"]


def _bare_draft(key: str, body: str) -> str:
    """What a request with no form answers drafts from ``body``."""
    return drafting.render_document(key, company=SAMPLE_VALUES["company"], counterparty=SAMPLE_VALUES["counterparty"],
                                    effective=SAMPLE_VALUES["effective_date"], fields={}, body=body)


def _counts(db: Session) -> tuple[int, int, int]:
    return tuple(db.scalar(select(func.count()).select_from(m)) for m in (PlaybookRun, PlaybookDeviation, Contract))


def _rows(db: Session, model, org_id: str) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.org_id == org_id))


def _job_of(db: Session, check_id: str) -> JobRun | None:
    return db.scalar(select(JobRun).where(JobRun.idempotency_key == f"template_check:{check_id}"))


def _run_job(db: Session, check_id: str) -> JobRun:
    """What the worker does with the job the request queued: ``_run_ai_job`` marks it running and
    counts the attempt, then hands it to the handler."""
    job = _job_of(db, check_id)
    assert job is not None, "the request queued no job"
    job.status, job.attempt_count, job.started_at = JobStatus.RUNNING, 1, utcnow()
    db.flush()
    asyncio.run(checks.run_check_job(db, job))
    return job


def _ask(api, key: str = "saas", **body) -> dict:
    r = api.post(f"{API}/{key}/checks", json=body)
    assert r.status_code == 202, r.text
    return r.json()


def test_the_old_synchronous_check_route_is_gone(api, db, monkeypatch):
    """The check used to be one blocking POST that paid for an AI call and stored nothing. If that
    route stayed beside the recorded one, there would be a second way to check that skips the record,
    the single flight and the status the page shows."""
    from app.main import app

    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    assert api.post(f"{API}/saas/check").status_code in (404, 405)
    assert api.post(f"{API}/saas/check", json={"body": "x {company} {counterparty}"}).status_code in (404, 405)
    assert not any(getattr(r, "path", "").endswith("/{key}/check") for r in app.routes)
    assert fake.calls == [] and _rows(db, DraftingTemplateCheck, user.org_id) == 0


@pytest.mark.parametrize(("method", "path"), [("post", "/saas/checks"), ("post", "/check-all")])
def test_checking_needs_the_permission_to_run_a_playbook(api, db, monkeypatch, method, path):
    """A person who may only view playbooks must not be able to start a paid AI review (FR-67), and
    the refusal has to come before any row, job or cost."""
    user = _org_user(db, permissions=["playbook:read"])
    _playbook(db, user, SAAS, _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    assert getattr(api, method)(API + path).status_code == 403

    assert fake.calls == [] and _rows(db, DraftingTemplateCheck, user.org_id) == 0
    assert _rows(db, JobRun, user.org_id) == 0


@pytest.mark.parametrize("path", ["/saas/checks", "/saas/checks/anything", "/options"])
def test_reading_checks_needs_the_permission_to_view_playbooks(api, db, path):
    """Findings quote our own paper and the playbook's rules; running reviews does not entitle anyone to read them (FR-66)."""
    user = _org_user(db, permissions=["playbook:run"])
    _as(user)
    assert api.get(API + path).status_code == 403


def test_asking_for_a_check_answers_202_with_the_recorded_check_and_pays_for_nothing(api, db, monkeypatch):
    """The page needs the check at once (it polls it) and the AI must not run inside the request:
    the review is the worker's job, handed over before the answer is sent."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    r = api.post(f"{API}/saas/checks")

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["state"] == "queued" and body["trigger"] == "manual" and body["template_key"] == "saas"
    assert body["unsaved"] is False and body["template_version"] == 0
    assert body["requested_by"] == "Checker" and body["on_behalf"] is False
    assert body["findings"] == [] and body["finished_at"] is None and body["playbook_name"] is None
    job = _job_of(db, body["id"])
    assert job is not None and job.celery_task_id, "the check was recorded but never handed to the worker"
    assert job.job_type == "template_check" and job.resource_type == "drafting_template_check"
    assert fake.calls == [] and not db.scalars(select(AISkillRun).where(AISkillRun.org_id == user.org_id)).all()


def test_the_job_reviews_the_saved_template_as_a_bare_request_drafts_it_and_stores_no_contract(api, db, monkeypatch):
    """The model has to read exactly what a drafted contract would be reviewed on, and a template has
    no contract: a run, a finding or a contract row for it would show up in every contract report."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput(deviations=[
        PlaybookDeviationOutput(rule_index=1, clause_type="limitation_of_liability", severity="high",
                                issue="The cap has an exception the playbook does not allow.")])))
    _as(user)
    before = _counts(db)
    check_id = _ask(api)["id"]

    _run_job(db, check_id)

    body = api.get(f"{API}/saas/checks/{check_id}").json()
    assert body["state"] == "completed" and body["playbook_name"] == SAAS and body["rule_count"] == 3
    assert [f["clause_type"] for f in body["findings"]] == ["limitation_of_liability"]
    assert body["findings"][0]["index"] == 0 and body["finding_count"] == 1 and body["highest_severity"] == "high"
    sent = fake.calls[0]["contract_context"].text
    assert sent == _bare_draft("saas", default_body("saas")) and "{" not in sent
    assert _counts(db) == before, "a template check must not create a run, a finding or a contract"


def test_check_reads_the_unsaved_text_from_the_editor(api, db, monkeypatch):
    """Checking before saving is the point of sending the editor's text; the saved wording must not change."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)
    draft = default_body("saas") + "\n\nSCHEDULE 9 — marker for {counterparty}."

    queued = _ask(api, body=draft)
    _run_job(db, queued["id"])

    assert queued["unsaved"] is True
    sent = fake.calls[0]["contract_context"].text
    assert f"SCHEDULE 9 — marker for {SAMPLE_VALUES['counterparty']}." in sent
    assert _rows(db, DraftingTemplateVersion, user.org_id) == 0
    assert "SCHEDULE 9" not in api.get(f"{API}/saas").json()["body"]


def test_check_reads_the_template_as_a_bare_request_drafts_it(api, db, monkeypatch):
    """A consultancy request with no answers drafts payment in 30 days, the playbook's preferred
    position. The editor's example value (45) would have made every consultancy check report a
    fallback that no fresh draft contains."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, "Consultancy Agreement Playbook", _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    _run_job(db, _ask(api, "consultancy")["id"])

    sent = fake.calls[0]["contract_context"].text
    assert sent == _bare_draft("consultancy", default_body("consultancy"))
    assert "within 30 days" in sent and "within 45 days" not in sent


def test_check_refuses_a_template_drafting_could_not_fill(api, db, monkeypatch):
    """Text that is not a valid template must be refused before a row, a job or a paid call exists."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    r = api.post(f"{API}/saas/checks", json={"body": "Dear {client_name},"})

    assert r.status_code == 422 and "Unknown placeholder" in r.json()["detail"]
    assert fake.calls == [], "no AI call (and no cost) for text that is not a valid template"
    assert _rows(db, DraftingTemplateCheck, user.org_id) == 0 and _rows(db, JobRun, user.org_id) == 0


def test_check_says_so_when_no_playbook_covers_the_template(api, db, monkeypatch):
    """No playbook means nothing to check against: no check row to show as 'checking' forever, no job."""
    user = _org_user(db, permissions=READ_RUN)  # this org has no NDA playbook
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    r = api.post(f"{API}/nda/checks")

    assert r.status_code == 409
    assert r.json()["detail"] == (
        f"No published playbook covers {TEMPLATES['nda']['name']}, so there is nothing to check it against.")
    assert fake.calls == []
    assert _rows(db, DraftingTemplateCheck, user.org_id) == 0 and _rows(db, JobRun, user.org_id) == 0
    assert not db.scalars(select(AISkillRun).where(AISkillRun.org_id == user.org_id)).all()


def test_check_names_the_pinned_playbook_that_is_gone(api, db, monkeypatch):
    """A template pinned to a deleted playbook must say so, not claim that no playbook exists by
    name: the person's fix is to publish or choose one, and the sentence tells them which."""
    user = _org_user(db, permissions=READ_RUN)
    gone, _ = _playbook(db, user, "Old NDA rules", _RULES)
    db.add(DraftingTemplateType(
        org_id=user.org_id, key="nda", name=TEMPLATES["nda"]["name"], label=TEMPLATES["nda"]["label"],
        form_key="new_agreement", agreement_kind="NDA", shipped=True, status="active", playbook_id=gone.id))
    gone.deleted_at = utcnow()
    db.flush()
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    r = api.post(f"{API}/nda/checks")

    assert r.status_code == 409
    assert "Old NDA rules" in r.json()["detail"] and "was deleted" in r.json()["detail"]
    assert _rows(db, DraftingTemplateCheck, user.org_id) == 0


def test_check_of_an_unknown_template_is_404(api, db):
    user = _org_user(db, permissions=READ_RUN)
    _as(user)
    for call in (lambda: api.post(f"{API}/customer/checks"), lambda: api.get(f"{API}/customer/checks"),
                 lambda: api.get(f"{API}/customer/checks/x")):
        r = call()
        assert r.status_code == 404 and r.json()["detail"] == "No such template"


def test_asking_again_for_the_same_wording_returns_the_waiting_check(api, db, monkeypatch):
    """Clicking Check now twice, or Check all after a click, must not queue a second paid review of
    wording that is already waiting for its turn."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    first, again = _ask(api), _ask(api)

    assert again["id"] == first["id"]
    assert _rows(db, DraftingTemplateCheck, user.org_id) == 1 and _rows(db, JobRun, user.org_id) == 1


def test_a_second_unsaved_check_is_refused_while_one_is_waiting(api, db, monkeypatch):
    """Unsaved text is not a wording anyone can join, so it cannot replace a waiting check
    silently; the person is told to wait (409) instead of paying for a review that is dropped."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)
    first = _ask(api)
    db.get(DraftingTemplateCheck, first["id"]).status = "running"
    db.flush()
    _ask(api, body=default_body("saas") + "\n\nOne.")

    r = api.post(f"{API}/saas/checks", json={"body": default_body("saas") + "\n\nTwo."})

    assert r.status_code == 409 and r.json()["detail"] == checks.QUEUE_FULL


def test_a_failed_review_is_recorded_on_the_check_and_leaks_nothing(api, db, monkeypatch):
    """The request already answered 202, so the failure can only live on the check. It must say the
    review did not run, never fall back to a phrase scan (the check that let template and playbook
    drift) and never carry the provider's error text, which can hold a key."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    _ai(monkeypatch, _FakeReview(RuntimeError("anthropic key sk-secret rejected")))
    _as(user)
    check_id = _ask(api)["id"]

    job = _run_job(db, check_id)

    r = api.get(f"{API}/saas/checks/{check_id}")
    body = r.json()
    assert body["state"] == "failed" and body["error_code"] == "ai_unavailable"
    assert "could not run" in body["error_message"] and "sk-secret" not in r.text
    assert job.status == JobStatus.FAILED and job.celery_task_id and "sk-secret" not in (job.error_message or "")


def test_the_checks_of_a_template_list_newest_first_with_unsaved_ones_flagged(api, db, monkeypatch):
    """History shows what was tried, including text that was never saved; a limit that cannot be
    honoured is a 422, not a silent default."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)
    saved = _ask(api)
    _run_job(db, saved["id"])
    unsaved = _ask(api, body=default_body("saas") + "\n\nExtra.")

    listed = api.get(f"{API}/saas/checks").json()

    assert [c["id"] for c in listed] == [unsaved["id"], saved["id"]]
    assert [c["unsaved"] for c in listed] == [True, False]
    assert [c["id"] for c in api.get(f"{API}/saas/checks?limit=1").json()] == [unsaved["id"]]
    assert api.get(f"{API}/saas/checks?limit=0").status_code == 422
    assert api.get(f"{API}/msa/checks").json() == []


def test_a_check_is_found_only_under_its_own_template_and_organisation(api, db, monkeypatch):
    """A check id from another template, or another organisation, must read as missing (FR-69)."""
    owner = _org_user(db, permissions=READ_RUN)
    _playbook(db, owner, SAAS, _RULES)
    _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(owner)
    check_id = _ask(api)["id"]
    assert api.get(f"{API}/saas/checks/{check_id}").status_code == 200
    other_template = api.get(f"{API}/msa/checks/{check_id}")
    assert other_template.status_code == 404 and other_template.json()["detail"] == "No such check."

    stranger = _org_user(db, permissions=READ_RUN)
    _as(stranger)
    foreign = api.get(f"{API}/saas/checks/{check_id}")
    assert foreign.status_code == 404 and foreign.json()["detail"] == "No such check."
    assert api.get(f"{API}/saas/checks").json() == []


def test_check_all_queues_one_check_for_each_covered_template_and_skips_the_rest(api, db, monkeypatch):
    """Check all must start exactly the reviews that can run: a second click while they are queued
    would otherwise pay again, and a template with no playbook must say so rather than queue."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    r = api.post(f"{API}/check-all")

    assert r.status_code == 202, r.text
    body = r.json()
    assert [s["key"] for s in body["started"]] == ["saas"]
    assert {(s["key"], s["reason"]) for s in body["skipped"]} == {
        (k, "no_playbook") for k in TEMPLATES if k != "saas"}
    check = db.get(DraftingTemplateCheck, body["started"][0]["check_id"])
    assert check.trigger == "check_all" and check.status == "queued" and _job_of(db, check.id) is not None

    again = api.post(f"{API}/check-all").json()
    assert again["started"] == [] and {"key": "saas", "reason": "checking"} in again["skipped"]
    assert _rows(db, DraftingTemplateCheck, user.org_id) == 1 and fake.calls == []
    starts = db.scalars(select(AuditLog).where(AuditLog.org_id == user.org_id,
                                               AuditLog.action == "drafting_template.check_all_started")
                        .order_by(AuditLog.created_at, AuditLog.id)).all()
    assert [(a.after["count"], a.after["keys"]) for a in starts] == [(1, ["saas"]), (0, [])]


def test_the_static_paths_are_not_taken_for_a_template_key(api, db):
    """Routes match in order: with ``/{key}`` first, GET /options would answer 'No such template'
    and the Change playbook picker would never load."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    _as(user)

    r = api.get(f"{API}/options")

    assert r.status_code == 200, r.text
    assert set(r.json()) == {"bindings", "playbooks"}
    assert [p["name"] for p in r.json()["playbooks"]] == [SAAS]
    assert api.post(f"{API}/check-all").status_code == 202


# --- end to end through the real controller (mock model), no patching of the AI layer -------------


def test_the_real_controller_runs_the_check_and_records_no_contract(api, db):
    """Runs run_structured_skill itself with the text-only context: the AI run row, citations and
    timeline all have to cope with a review that has no contract behind it."""
    user = _org_user(db, permissions=READ_RUN)
    _playbook(db, user, SAAS, _RULES)
    _as(user)
    check_id = _ask(api)["id"]

    _run_job(db, check_id)

    assert api.get(f"{API}/saas/checks/{check_id}").json()["state"] == "completed"
    run = db.scalar(select(AISkillRun).where(AISkillRun.org_id == user.org_id,
                                             AISkillRun.skill_name == "playbook_review"))
    assert run is not None
    assert run.resource_type == "drafting_template_check" and run.resource_id == check_id
    assert run.contract_id is None and run.contract_version_id is None and run.text_snapshot_id is None


# --- publishing a playbook checks the templates it reviews ---------------------------------------


def _publish(api, playbook: Playbook):
    return api.post(f"/api/v1/playbooks/{playbook.id}/publish")


def _spy_hook(db: Session, monkeypatch, *, raises: bool = False) -> dict:
    """Record the order of ``db.commit`` and ``on_playbooks_published``, and what the hook got."""
    seen: dict = {"events": [], "calls": []}
    real_commit = db.commit

    def commit():
        seen["events"].append("commit")
        real_commit()

    def hook(session, **kwargs):
        seen["events"].append("hook")
        seen["calls"].append({**kwargs, "details": {p: kwargs["detail_for"](p) for p in kwargs["playbook_ids"]}})
        if raises:
            raise RuntimeError("the broker is down")

    monkeypatch.setattr(db, "commit", commit)
    monkeypatch.setattr(checks, "on_playbooks_published", hook)
    return seen


def test_publishing_asks_for_the_template_checks_only_after_the_publish_committed(api, db, monkeypatch):
    """The checks read the published version from the database. Asked before the commit they would
    read the old one (or none), and a failure in them would roll the publish back."""
    user = _org_user(db, permissions=["playbook:publish"])
    playbook, version = _playbook(db, user, SAAS, _RULES)
    seen = _spy_hook(db, monkeypatch)
    _as(user)

    r = _publish(api, playbook)

    assert r.status_code == 200, r.text
    assert seen["events"][-2:] == ["commit", "hook"]
    (call,) = seen["calls"]
    assert call["org_id"] == user.org_id and call["actor_user_id"] == user.id
    assert call["playbook_ids"] == [playbook.id]
    assert call["details"] == {playbook.id: {"playbook_id": playbook.id, "playbook_version_id": version.id,
                                            "version_number": 1}}


def test_publishing_queues_a_check_of_the_template_that_playbook_reviews(api, db, monkeypatch):
    """The real hook, not a stand-in: publishing the SaaS playbook must leave a queued
    'playbook_published' check of the SaaS template, naming the version, and touch no other template."""
    user = _org_user(db, permissions=["playbook:publish"])
    playbook, version = _playbook(db, user, SAAS, _RULES)
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    assert _publish(api, playbook).status_code == 200

    (check,) = db.scalars(select(DraftingTemplateCheck).where(DraftingTemplateCheck.org_id == user.org_id)).all()
    assert check.template_key == "saas" and check.trigger == "playbook_published" and check.status == "queued"
    assert check.trigger_detail == {"playbook_id": playbook.id, "playbook_version_id": version.id,
                                    "version_number": 1}
    assert _job_of(db, check.id) is not None and fake.calls == []


def test_a_failing_template_hook_never_fails_the_publish(api, db, monkeypatch):
    """The playbook is already published when the hook runs. A broker or database hiccup there must
    be logged, not turned into a 500 that tells the person their publish failed."""
    user = _org_user(db, permissions=["playbook:publish"])
    playbook, _ = _playbook(db, user, SAAS, _RULES)
    seen = _spy_hook(db, monkeypatch, raises=True)
    _as(user)

    r = _publish(api, playbook)

    assert r.status_code == 200, r.text
    assert r.json()["id"] == playbook.id and r.json()["status"] == "published"
    assert seen["events"][-2:] == ["commit", "hook"]


def test_seeding_the_library_asks_for_the_template_checks_after_the_commit_for_what_it_added(api, db, monkeypatch):
    """Seeding publishes a playbook per agreement type, and the templates they review have never
    been checked against them. Only the playbooks this call added are announced: a second call that
    adds nothing must not start a round of paid reviews."""
    user = _org_user(db, permissions=["playbook:publish"])
    seen = _spy_hook(db, monkeypatch)
    _as(user)

    first = api.post("/api/v1/playbooks/seed-library")

    assert first.status_code == 200, first.text
    added = first.json()["added"]
    assert added, "an empty organisation gets the whole library"
    ids = {p.id for p in db.scalars(select(Playbook).where(Playbook.org_id == user.org_id,
                                                           Playbook.name.in_(added)))}
    assert seen["events"][-2:] == ["commit", "hook"]
    (call,) = seen["calls"]
    assert set(call["playbook_ids"]) == ids and len(ids) == len(added)
    assert all(d["version_number"] == 1 for d in call["details"].values())

    assert api.post("/api/v1/playbooks/seed-library").json() == {"added": []}
    assert len(seen["calls"]) == 1, "nothing was added the second time, so nothing is announced"


def test_seeding_the_library_checks_every_template_its_playbooks_review(api, db, monkeypatch):
    """The real hook after a seed: each shipped template whose playbook was just added gets a
    queued check, none is paid for yet, and a failing hook cannot fail the seed."""
    user = _org_user(db, permissions=["playbook:publish"])
    fake = _ai(monkeypatch, _FakeReview(PlaybookReviewOutput()))
    _as(user)

    r = api.post("/api/v1/playbooks/seed-library")

    assert r.status_code == 200, r.text
    rows = db.scalars(select(DraftingTemplateCheck).where(DraftingTemplateCheck.org_id == user.org_id)).all()
    # (The library ships no NDA playbook, so the NDA template has nothing new to be checked against.)
    assert {"msa", "saas", "dpa", "vendor", "sow", "consultancy"} <= {c.template_key for c in rows}
    assert {c.trigger for c in rows} == {"playbook_published"} and {c.status for c in rows} == {"queued"}
    assert fake.calls == []

    seen = _spy_hook(db, monkeypatch, raises=True)
    other = _org_user(db, permissions=["playbook:publish"])
    _as(other)
    again = api.post("/api/v1/playbooks/seed-library")
    assert again.status_code == 200 and again.json()["added"] and seen["calls"]


# --- the NDA request form ------------------------------------------------------------------------


def test_the_nda_form_offers_no_term_shorter_than_the_playbooks_two_years():
    """The NDA playbook sets a two-year floor. A 1-year option let a requester pick a term the
    playbook then flagged on a contract drafted from our own template."""
    form = form_def("new_agreement")
    field = next((f for f in form["fields"] if f["key"] == "nda_term"), None) if form else None
    assert field is not None, "the NDA term question moved: update this test, do not delete it"
    years = [int(o.split()[0]) for o in field["options"]]
    assert years and min(years) >= 2


def test_every_template_still_fills_after_the_wording_changes():
    for key in TEMPLATES:
        text = render_sample(default_body(key))
        assert "{" not in text and "}" not in text, key
