"""The drafting model's assumptions are a note for the drafter, never contract text.

They used to be rendered as a "Drafting Assumptions" section at the end of every
AI-drafted contract, so they landed in the text snapshot that clause extraction,
playbook review and citations read — and the prompts told the model to fill any
unspecified term with a "market-standard default", i.e. invent terms no playbook
backs. Now gaps stay visible as [TO BE CONFIRMED: ...] and the assumptions go to
the drafter (tool result, run thread, timeline)."""

from __future__ import annotations

import inspect
import uuid
from io import BytesIO

import pytest
from docx import Document
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.ai.prompt_versions import DEFAULT_SKILL_PROMPTS
from app.ai.schemas import ContractDocxGenerationOutput, ContractDocxSection
from app.ai.tool_runtime import ToolRuntime, _render_structured_contract_docx
from app.auth.models import User
from app.contracts.models import Contract
from app.core.database import engine, new_uuid, utcnow
from app.intake.drafting import DraftingService
from app.intake.models import IntakeRequest
from app.organizations.models import Organization
from app.workflows.models import WorkflowRun

ASSUMPTION = "Term assumed 2 years"


def _drafted() -> ContractDocxGenerationOutput:
    return ContractDocxGenerationOutput(
        title="Distribution Agreement",
        sections=[ContractDocxSection(heading="Term", body="[TO BE CONFIRMED: term length]")],
        assumptions=[ASSUMPTION, "  "],
    )


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
def fake_skill(monkeypatch):
    from app.ai.controller import ai_controller

    async def _run(db, **kwargs):
        return _drafted()

    monkeypatch.setattr(ai_controller, "run_structured_skill", _run)


def test_rendered_contract_carries_no_assumptions():
    """Guards: the model's assumptions printed into the contract body, then read
    as a clause by extraction and playbook review."""
    body_text, content = _render_structured_contract_docx(
        title="Distribution Agreement", sections=[("Term", "[TO BE CONFIRMED: term length]")]
    )
    paragraphs = "\n".join(p.text for p in Document(BytesIO(content)).paragraphs)
    for text in (body_text, paragraphs):
        assert "Drafting Assumptions" not in text
        assert "[TO BE CONFIRMED: term length]" in text
    assert "assumptions" not in inspect.signature(_render_structured_contract_docx).parameters


def test_no_drafting_instruction_invites_invented_market_terms():
    """Guards: the model told to fill gaps with market-standard terms no playbook backs."""
    prompt = DEFAULT_SKILL_PROMPTS["contract_docx_generation"]
    assert "[TO BE CONFIRMED:" in prompt
    assert "never printed" in prompt
    assert "market-standard default" not in inspect.getsource(DraftingService._ai_draft_text)
    redraft = inspect.getsource(ToolRuntime._redraft_contract)
    assert "market-standard contract" not in redraft
    assert "keep every [TO BE CONFIRMED" in redraft


async def test_intake_bespoke_draft_returns_assumptions_beside_the_text(fake_skill):
    """Guards: an intake bespoke draft silently dropping the note or printing it into the text."""
    service = DraftingService(db=None)
    request = IntakeRequest(id="r1", type_label="Distribution", description="", field_values={})
    actor = User(id="u1", org_id="o1")
    text, assumptions = await service._ai_draft_text(
        actor=actor, request=request, company="Us", counterparty="Them", label="Distribution", fields={},
    )
    assert ASSUMPTION not in text
    assert "[TO BE CONFIRMED: term length]" in text
    assert assumptions == [ASSUMPTION]


async def test_custom_draft_posts_assumptions_to_the_run_thread(db, fake_skill):
    """Guards: assumptions hidden from the drafter — the custom draft must post them
    on the request's workflow thread as "system", and keep them out of the contract."""
    org = Organization(id=new_uuid(), name="Draft Co", slug=f"draft-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    actor = User(org_id=org.id, email=f"d-{uuid.uuid4().hex[:8]}@example.com", full_name="Drafter",
                 hashed_password="h", status="active")
    db.add(actor)
    db.flush()
    req = IntakeRequest(
        org_id=org.id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form", requester_user_id=actor.id,
        type_label="Distribution", description="t", field_values={}, priority="Medium", status="open",
        stage="new", submitted_at=utcnow(), created_by_user_id=actor.id, updated_by_user_id=actor.id,
    )
    db.add(req)
    db.flush()
    run = WorkflowRun(org_id=org.id, request_id=req.id, flow_name="F", flow_version=1,
                      steps=[], status="running", current_index=0)
    db.add(run)
    db.flush()

    uploaded: list[str] = []
    service = DraftingService(db)

    async def _create(*, upload, user, title, **kwargs):
        uploaded.append(upload.file.read().decode("utf-8"))
        contract = Contract(org_id=org.id, title=title, owner_user_id=user.id,
                            created_by_user_id=user.id, updated_by_user_id=user.id)
        db.add(contract)
        db.flush()
        return {"contract": contract}

    service.files.create_contract_from_upload = _create
    await service.draft_contract_for_request(actor=actor, request=req, custom=True)

    assert ASSUMPTION not in uploaded[0]
    comments = (run.context or {}).get("comments") or []
    assert len(comments) == 1
    assert comments[0]["actor_id"] is None and comments[0]["actor_name"] == "system"
    assert f"- {ASSUMPTION}" in comments[0]["text"]


def test_the_assistant_is_told_to_relay_drafting_assumptions():
    """Assumptions left the contract text; for assistant-drafted contracts the chat reply is
    the only place a person sees them, so the assistant must list them, not drop them."""
    prompt = DEFAULT_SKILL_PROMPTS["assistant_streaming"]
    assert "returns assumptions, list every one for the user" in prompt
