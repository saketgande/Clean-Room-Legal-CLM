"""Check Approvers must show who will really approve, before anything is filed.

The wizard showed a table typed into the page: approvers picked by value alone,
a fixed reviewer name ("Bhavya Murgai"), "Delegated" for every request. None of
it came from the workflow that actually runs the approval. The preview now runs
the same workflow selection and step conditions filing does.
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake.models import IntakeRequest
from app.intake.service import preview_approvals
from app.workflows.models import Workflow

_KEYWORD = f"previewcheck{uuid.uuid4().hex[:8]}"


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        for w in s.scalars(select(Workflow).where(Workflow.name.like("test-preview%"))):
            s.delete(w)
        s.commit()
        s.close()


@pytest.fixture
def actor(db):
    user = db.scalar(select(User).order_by(User.created_at))
    if user is None:
        pytest.skip("needs a seeded user")
    return user


@pytest.fixture
def flow(db, actor):
    """Matches only our keyword; Finance approves, Executive only above ₹1 crore."""
    w = Workflow(id=str(uuid.uuid4()), org_id=actor.org_id, name="test-preview flow", enabled=True,
                 is_builtin=False, eval_order=1, version=1, criteria={"match_keyword": _KEYWORD},
                 steps=[
                     {"id": "a", "type": "human_task", "name": "Review", "config": {}},
                     {"id": "b", "type": "approval", "name": "Finance sign-off",
                      "config": {"approver_role": "finance"}},
                     {"id": "c", "type": "approval", "name": "Executive sign-off",
                      "config": {"approver_role": "gc"},
                      "cond": {"field": "value", "op": "gt", "value": 10_000_000}},
                 ])
    db.add(w)
    db.commit()
    return w


def _preview(db, actor, value):
    return preview_approvals(db, actor=actor, payload=SimpleNamespace(
        type_label="Contract Question", description=f"please {_KEYWORD}", priority="Medium",
        department=None, field_values={"value": value}))


def test_the_preview_is_the_workflows_own_approval_steps(db, actor, flow):
    out = _preview(db, actor, "50000")
    assert out["workflow"]["name"] == "test-preview flow"
    assert [a["step_name"] for a in out["approvers"]] == ["Finance sign-off"]


def test_a_step_whose_condition_is_met_is_included(db, actor, flow):
    """"Run only when value > ₹1 crore" — the preview applies it as the engine does."""
    out = _preview(db, actor, "25000000")
    assert [a["step_name"] for a in out["approvers"]] == ["Finance sign-off", "Executive sign-off"]


def test_previewing_saves_nothing(db, actor, flow):
    before = db.scalar(select(func.count()).select_from(IntakeRequest))
    _preview(db, actor, "1")
    db.commit()
    assert db.scalar(select(func.count()).select_from(IntakeRequest)) == before
