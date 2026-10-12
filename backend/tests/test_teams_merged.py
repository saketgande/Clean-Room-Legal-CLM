"""Teams are the one list of people groups: owners, reviewers and approvers.

Intake teams ("pools"), approver groups and a fixed department list in the
workflow builder were three lists, joined by hard-coded name lookups, so the
team picked on a workflow step was often not the one that acted. These guard
the merged model.
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.models  # noqa: F401  (registers every mapper)
from app.auth.models import User
from app.core.database import SessionLocal
from app.intake import teams
from app.intake.models import IntakeTeam
from app.intake.service import _pick_owner_team
from app.workflows.models import Workflow

_TAG = "test-teams-merged"


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        for w in s.scalars(select(Workflow).where(Workflow.name.like(f"{_TAG}%"))):
            s.delete(w)
        s.flush()
        for t in s.scalars(select(IntakeTeam).where(IntakeTeam.key.like("tm_%"))):
            s.delete(t)
        s.commit()
        s.close()


@pytest.fixture
def actor(db):
    user = db.scalar(select(User).order_by(User.created_at))
    if user is None:
        pytest.skip("needs a seeded user")
    return user


def test_every_org_has_the_default_teams_and_one_default_intake_team(db, actor):
    teams.ensure_default_teams(db, org_id=actor.org_id)
    teams.ensure_default_teams(db, org_id=actor.org_id)  # idempotent
    rows = db.scalars(select(IntakeTeam).where(IntakeTeam.org_id == actor.org_id)).all()
    keys = [t.key for t in rows]
    for key, _name, _desc in teams.DEFAULT_TEAMS:
        assert keys.count(key) == 1
    assert sum(1 for t in rows if t.is_default_intake) == 1


def test_an_unmatched_request_goes_to_the_default_intake_team(db, actor):
    """Replaces the old Tier 1 / Tier 2 complexity fallback."""
    teams.ensure_default_teams(db, org_id=actor.org_id)
    default = teams.default_intake_team(db, org_id=actor.org_id)
    picked = _pick_owner_team(db, org_id=actor.org_id, category="no-such-category", department=None)
    assert picked is not None and picked.id == default.id


def test_a_team_a_workflow_uses_cannot_be_deleted(db, actor):
    """A step naming a deleted team would ask nobody."""
    t = IntakeTeam(org_id=actor.org_id, key=f"tm_{uuid.uuid4().hex[:6]}", name=f"{_TAG} team")
    db.add(t)
    db.flush()
    db.add(Workflow(id=str(uuid.uuid4()), org_id=actor.org_id, name=f"{_TAG} flow", enabled=False,
                    is_builtin=False, eval_order=999, version=1, criteria={},
                    steps=[{"id": "s", "type": "approval", "name": "Sign-off", "config": {"team_id": t.id}}]))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        teams.delete_team(db, actor=actor, team_id=t.id)
    assert exc.value.status_code == 409 and "Sign-off" in exc.value.detail
    assert any(u["where"].endswith("Sign-off") for u in teams.serialize_team(db, t)["used_in"])
