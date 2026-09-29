"""The Legal Intake "All requests" pool and its "Assign to me" claim action.

Pool visibility (``service.list_pool_requests``): open + unassigned only,
scoped by role (`_accessible`) and then narrowed by the viewer's active team
membership via ``IntakeTeam.departments`` — a field the admin Teams UI
already exposes ("Serves (business units) ... Blank = serves all") — OR
``IntakeTeam.expertise`` matching the request's AI-triaged category, since
``department`` is the requester's own business unit (a different vocabulary
from a team's "Serves" list) and can never be relied on alone.

Claiming (``service.claim_request``): the manual, self-targeting counterpart
to the auto-balancer's ``pick_from_pool``. Covers the same guard shape as
``record_triage_action``'s "reassigned" branch, plus the two failure modes
specific to a pool claim: someone else got there first (409), and claiming
outside your team's department scope (403).

Follows the fixture/helper conventions of ``test_approval_chain_reroute.py``:
a real (migrated) Postgres session in one connection-level transaction,
rolled back at teardown; org/role/user/grant helpers copied from that file
since RBAC setup (``UserRoleGrant`` IS the ``user_role`` table `User.roles`
reads from) is identical here.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.auth.models import Permission, Role, User, UserRoleGrant
from app.core.database import engine, new_uuid, utcnow
from app.intake import service as intake_service
from app.intake.models import IntakeRequest, IntakeTeam, IntakeTeamMember
from app.org_structure.models import OrgUnit
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


def _get_or_create_permission(db: Session, value: str) -> Permission:
    existing = db.query(Permission).filter(Permission.value == value).one_or_none()
    if existing is not None:
        return existing
    perm = Permission(value=value)
    db.add(perm)
    db.flush()
    return perm


def _make_org(db: Session) -> Organization:
    org = Organization(id=new_uuid(), name="Pool Co", slug=f"pool-co-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.flush()
    return org


def _make_root_unit(db: Session, *, org_id: str) -> OrgUnit:
    unit = OrgUnit(org_id=org_id, name="Root", parent_id=None)
    db.add(unit)
    db.flush()
    return unit


def _make_role(db: Session, *, org_id: str, name: str, permission_values: list[str] | None = None) -> Role:
    role = Role(org_id=org_id, name=f"{name}-{uuid.uuid4().hex[:8]}", allows_hierarchy_rollup=True)
    role.permissions = [_get_or_create_permission(db, v) for v in (permission_values or [])]
    db.add(role)
    db.flush()
    return role


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


def _make_grant(db: Session, *, user: User, role: Role, org_unit: OrgUnit) -> UserRoleGrant:
    grant = UserRoleGrant(user_id=user.id, role_id=role.id, org_id=user.org_id, org_unit_id=org_unit.id)
    db.add(grant)
    db.flush()
    return grant


def _staff_user(db: Session, *, org_id: str, root: OrgUnit, label: str) -> User:
    """A user with intake:read (the pool-visible "staff" gate)."""
    role = _make_role(db, org_id=org_id, name=f"staff-{label}", permission_values=["intake:read"])
    user = _make_user(db, org_id=org_id, label=label)
    _make_grant(db, user=user, role=role, org_unit=root)
    return user


def _make_team(db: Session, *, org_id: str, departments: list[str] | None,
               expertise: list[str] | None = None, name: str = "Team") -> IntakeTeam:
    team = IntakeTeam(
        org_id=org_id, key=f"team-{uuid.uuid4().hex[:6]}", name=name,
        departments=departments, expertise=expertise or [],
    )
    db.add(team)
    db.flush()
    return team


def _add_member(db: Session, *, org_id: str, team_id: str, user_id: str, capacity: int = 0) -> IntakeTeamMember:
    member = IntakeTeamMember(org_id=org_id, team_id=team_id, user_id=user_id, active=True, capacity=capacity)
    db.add(member)
    db.flush()
    return member


def _make_request(db: Session, *, org_id: str, requester_id: str, department: str | None,
                  status: str = "open", ai_triage: dict | None = None) -> IntakeRequest:
    req = IntakeRequest(
        org_id=org_id, ref=f"REQ-{uuid.uuid4().hex[:6]}", source="form",
        requester_user_id=requester_id, type_label="General request", description="test",
        field_values={}, priority="Medium", status=status, stage="new", department=department,
        ai_triage=ai_triage, submitted_at=utcnow(),
        created_by_user_id=requester_id, updated_by_user_id=requester_id,
    )
    db.add(req)
    db.flush()
    return req


def test_pool_shows_assigned_and_unassigned_but_excludes_closed(db: Session):
    """Admin (and anyone else scoped to see it) sees the whole open queue —
    assigned rows included, carrying their assignee — not just the unclaimed
    ones. Only terminal (closed/approved) requests drop out."""
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    staff = _staff_user(db, org_id=org.id, root=root, label="staff")
    requester = _make_user(db, org_id=org.id, label="requester")

    unassigned = _make_request(db, org_id=org.id, requester_id=requester.id, department=None)
    assigned = _make_request(db, org_id=org.id, requester_id=requester.id, department=None)
    assigned.assigned_to_user_id = staff.id
    closed = _make_request(db, org_id=org.id, requester_id=requester.id, department=None, status="closed")
    db.flush()

    pool = intake_service.list_pool_requests(db, user=staff)
    by_id = {r["id"]: r for r in pool}
    assert unassigned.id in by_id
    assert by_id[unassigned.id]["assigned_to_user_id"] is None
    assert assigned.id in by_id
    assert by_id[assigned.id]["assigned_to_user_id"] == staff.id
    assert closed.id not in by_id


def test_request_with_no_department_is_visible_to_every_team(db: Session):
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    ip_staff = _staff_user(db, org_id=org.id, root=root, label="ip-staff")
    ip_team = _make_team(db, org_id=org.id, departments=["Trademarks"])
    _add_member(db, org_id=org.id, team_id=ip_team.id, user_id=ip_staff.id)

    orphan = _make_request(db, org_id=org.id, requester_id=requester.id, department=None)
    db.flush()

    pool = intake_service.list_pool_requests(db, user=ip_staff)
    assert orphan.id in {r["id"] for r in pool}


def test_team_with_configured_departments_only_sees_matching_requests(db: Session):
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    ip_staff = _staff_user(db, org_id=org.id, root=root, label="ip-staff")
    ip_team = _make_team(db, org_id=org.id, departments=["Trademarks"])
    _add_member(db, org_id=org.id, team_id=ip_team.id, user_id=ip_staff.id)

    matching = _make_request(db, org_id=org.id, requester_id=requester.id, department="Trademarks")
    other = _make_request(db, org_id=org.id, requester_id=requester.id, department="Finance")
    db.flush()

    ids = {r["id"] for r in intake_service.list_pool_requests(db, user=ip_staff)}
    assert matching.id in ids
    assert other.id not in ids


def test_team_with_blank_departments_serves_all(db: Session):
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    generalist = _staff_user(db, org_id=org.id, root=root, label="generalist")
    catch_all_team = _make_team(db, org_id=org.id, departments=[])
    _add_member(db, org_id=org.id, team_id=catch_all_team.id, user_id=generalist.id)

    finance_req = _make_request(db, org_id=org.id, requester_id=requester.id, department="Finance")
    trademarks_req = _make_request(db, org_id=org.id, requester_id=requester.id, department="Trademarks")
    db.flush()

    ids = {r["id"] for r in intake_service.list_pool_requests(db, user=generalist)}
    assert finance_req.id in ids
    assert trademarks_req.id in ids


def test_staff_not_on_any_team_sees_the_unrestricted_pool(db: Session):
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    staff = _staff_user(db, org_id=org.id, root=root, label="staff")

    finance_req = _make_request(db, org_id=org.id, requester_id=requester.id, department="Finance")
    db.flush()

    ids = {r["id"] for r in intake_service.list_pool_requests(db, user=staff)}
    assert finance_req.id in ids


def test_claim_assigns_and_the_request_still_shows_with_the_new_assignee(db: Session):
    """Claiming doesn't remove the row from the queue — it still shows up,
    now carrying the claimer as its assignee (the frontend switches its
    action cell from "Assign to me" to the assignee's name)."""
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    staff = _staff_user(db, org_id=org.id, root=root, label="staff")
    req = _make_request(db, org_id=org.id, requester_id=requester.id, department=None)
    db.flush()

    result = intake_service.claim_request(db, actor=staff, request_id=req.id)

    assert result["assigned_to_user_id"] == staff.id
    by_id = {r["id"]: r for r in intake_service.list_pool_requests(db, user=staff)}
    assert req.id in by_id
    assert by_id[req.id]["assigned_to_user_id"] == staff.id


def test_claiming_an_already_assigned_request_conflicts(db: Session):
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    first = _staff_user(db, org_id=org.id, root=root, label="first")
    second = _staff_user(db, org_id=org.id, root=root, label="second")
    req = _make_request(db, org_id=org.id, requester_id=requester.id, department=None)
    db.flush()

    intake_service.claim_request(db, actor=first, request_id=req.id)

    with pytest.raises(HTTPException) as exc:
        intake_service.claim_request(db, actor=second, request_id=req.id)
    assert exc.value.status_code == 409


def test_claiming_outside_your_teams_departments_is_forbidden(db: Session):
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    ip_staff = _staff_user(db, org_id=org.id, root=root, label="ip-staff")
    ip_team = _make_team(db, org_id=org.id, departments=["Trademarks"])
    _add_member(db, org_id=org.id, team_id=ip_team.id, user_id=ip_staff.id)

    finance_req = _make_request(db, org_id=org.id, requester_id=requester.id, department="Finance")
    db.flush()

    with pytest.raises(HTTPException) as exc:
        intake_service.claim_request(db, actor=ip_staff, request_id=finance_req.id)
    assert exc.value.status_code == 403


def test_mismatched_department_but_matching_expertise_is_still_visible(db: Session):
    """Regression for REQ-4037: `department` on the request is the requester's
    own business unit (e.g. "Enterprise Systems", picked off the intake form),
    a different vocabulary from a team's "Serves" list — it will never equal
    "Legal". A request whose AI-triaged category is in the team's expertise
    must still show up, or every legal-routed request becomes invisible to
    every legal team."""
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    legal_staff = _staff_user(db, org_id=org.id, root=root, label="legal-staff")
    legal_team = _make_team(db, org_id=org.id, departments=["Legal"], expertise=["Contract Review"])
    _add_member(db, org_id=org.id, team_id=legal_team.id, user_id=legal_staff.id)

    req = _make_request(
        db, org_id=org.id, requester_id=requester.id, department="Enterprise Systems",
        ai_triage={"category": "Contract Review"},
    )
    unrelated = _make_request(
        db, org_id=org.id, requester_id=requester.id, department="Enterprise Systems",
        ai_triage={"category": "Vendor"},
    )
    db.flush()

    ids = {r["id"] for r in intake_service.list_pool_requests(db, user=legal_staff)}
    assert req.id in ids
    assert unrelated.id not in ids


def test_claim_allowed_via_expertise_match_despite_department_mismatch(db: Session):
    org = _make_org(db)
    root = _make_root_unit(db, org_id=org.id)
    requester = _make_user(db, org_id=org.id, label="requester")
    legal_staff = _staff_user(db, org_id=org.id, root=root, label="legal-staff")
    legal_team = _make_team(db, org_id=org.id, departments=["Legal"], expertise=["Contract Review"])
    _add_member(db, org_id=org.id, team_id=legal_team.id, user_id=legal_staff.id)

    req = _make_request(
        db, org_id=org.id, requester_id=requester.id, department="Enterprise Systems",
        ai_triage={"category": "Contract Review"},
    )
    db.flush()

    result = intake_service.claim_request(db, actor=legal_staff, request_id=req.id)
    assert result["assigned_to_user_id"] == legal_staff.id


def test_assign_owner_skips_a_fully_loaded_tied_team_for_one_with_room(db: Session):
    """Two teams tie on expertise and sort_order; the one that sorts first
    alphabetically has a single member already at capacity. Regression for
    REQ-4037: the old single-candidate pick committed to that tie and gave up
    entirely instead of trying the next equally-valid team with an open seat."""
    org = _make_org(db)
    requester = _make_user(db, org_id=org.id, label="requester")

    full_team = _make_team(db, org_id=org.id, departments=["Legal"], expertise=["Contract Review"], name="A team")
    open_team = _make_team(db, org_id=org.id, departments=["Legal"], expertise=["Contract Review"], name="B team")

    full_member = _make_user(db, org_id=org.id, label="full-member")
    _add_member(db, org_id=org.id, team_id=full_team.id, user_id=full_member.id, capacity=1)
    open_member = _make_user(db, org_id=org.id, label="open-member")
    _add_member(db, org_id=org.id, team_id=open_team.id, user_id=open_member.id, capacity=1)

    # Fill the first (alphabetically-first) team's only member to capacity.
    filler = _make_request(db, org_id=org.id, requester_id=requester.id, department=None)
    filler.assigned_to_user_id = full_member.id
    db.flush()

    req = _make_request(
        db, org_id=org.id, requester_id=requester.id, department="Enterprise Systems",
        ai_triage={"category": "Contract Review", "complexity": "standard"},
    )
    db.flush()

    intake_service._assign_owner_from_triage(db, req)

    assert req.assigned_to_user_id == open_member.id
