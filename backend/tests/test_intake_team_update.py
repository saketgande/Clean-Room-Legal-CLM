"""Regression: editing a team (e.g. just its departments from the admin UI)
while re-submitting an already-existing member used to 500 with a
psycopg.errors.UniqueViolation on uq_intake_team_member_team_user.

Root cause: IntakeTeam.members is `cascade="all, delete-orphan"`, and
teams._apply_members replaced the whole collection with brand-new
IntakeTeamMember objects on every save. SQLAlchemy schedules the INSERTs for
the "new" (team_id, user_id) rows before the DELETEs for the orphaned old
ones on a same-table collection replacement, so re-submitting a member who
was already on the team collided with their own about-to-be-deleted row.
Fixed by reusing the existing member row (update in place) instead of always
constructing a fresh one.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import User
from app.core.database import engine, new_uuid
from app.intake import teams as teams_service
from app.intake.models import IntakeTeamMember
from app.intake.schemas import TeamCreate, TeamMemberSpec, TeamUpdate
from app.organizations.models import Organization


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


def _make_org(db: Session) -> Organization:
    org = Organization(id=new_uuid(), name="Teams Co", slug=f"teams-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _make_user(db: Session, *, org_id: str, label: str) -> User:
    user = User(
        org_id=org_id,
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.com",
        full_name=label,
        hashed_password="hash",
        status="active",
    )
    db.add(user)
    db.flush()
    return user


def test_updating_departments_while_resubmitting_an_existing_member_does_not_conflict(db: Session):
    org = _make_org(db)
    admin = _make_user(db, org_id=org.id, label="admin")
    member = _make_user(db, org_id=org.id, label="member")

    created = teams_service.create_team(
        db, actor=admin,
        payload=TeamCreate(
            key="ip", name="IP Team", departments=["Trademarks"],
            members=[TeamMemberSpec(user_id=member.id, capacity=5, active=True)],
        ),
    )
    db.commit()

    # Re-submit the SAME member (this is what the admin UI's "Serves
    # (business units)" save does — the whole form, including unchanged
    # members) while only actually changing `departments`.
    updated = teams_service.update_team(
        db, actor=admin, team_id=created["id"],
        payload=TeamUpdate(
            departments=["Trademarks", "Patents"],
            members=[TeamMemberSpec(user_id=member.id, capacity=5, active=True)],
        ),
    )
    db.commit()

    assert updated["departments"] == ["Trademarks", "Patents"]
    rows = db.query(IntakeTeamMember).filter(IntakeTeamMember.team_id == created["id"]).all()
    assert len(rows) == 1
    assert rows[0].user_id == member.id


def test_update_team_still_removes_members_dropped_from_the_payload(db: Session):
    org = _make_org(db)
    admin = _make_user(db, org_id=org.id, label="admin")
    stays = _make_user(db, org_id=org.id, label="stays")
    leaves = _make_user(db, org_id=org.id, label="leaves")

    created = teams_service.create_team(
        db, actor=admin,
        payload=TeamCreate(
            key="tax", name="Tax Team",
            members=[
                TeamMemberSpec(user_id=stays.id, capacity=5, active=True),
                TeamMemberSpec(user_id=leaves.id, capacity=5, active=True),
            ],
        ),
    )
    db.commit()

    teams_service.update_team(
        db, actor=admin, team_id=created["id"],
        payload=TeamUpdate(members=[TeamMemberSpec(user_id=stays.id, capacity=5, active=True)]),
    )
    db.commit()

    user_ids = {
        m.user_id for m in
        db.query(IntakeTeamMember).filter(IntakeTeamMember.team_id == created["id"]).all()
    }
    assert user_ids == {stays.id}
