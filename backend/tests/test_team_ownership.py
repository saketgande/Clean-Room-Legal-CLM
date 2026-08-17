"""Area 3 — teams, and the one department vocabulary.

The audit found "which team owns this work?" was unanswerable anywhere in the
system, and that the app carried two unrelated department vocabularies.

    3.1  routing resolved a pool to ONE person and discarded the pool
    3.6  intake captured free-text departments from a hardcoded frontend list
         that shared no values with the list the access model reasons about

Not fixed here, and deliberately: IntakeTeam and ApproverGroup remain two
separate membership tables. See the module docstring note at the bottom.

House style: no database, no fixtures framework.
"""

import inspect
import pathlib

import app.models  # noqa: F401 — registers every table so FKs resolve
from app.intake.models import IntakeRequest

FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


# ==========================================================================
# 3.1 — the routed pool is remembered
# ==========================================================================

def test_request_records_the_pool_it_was_routed_to():
    col = IntakeRequest.__table__.columns.get("routed_team_id")
    assert col is not None, "the team is still discarded at routing"
    assert col.nullable is True
    assert col.index is True
    fk = next(iter(col.foreign_keys))
    assert fk.column.table.name == "intake_team"
    assert fk.ondelete == "SET NULL"


def test_routing_captures_the_team_at_the_chokepoint():
    """pick_from_pool has always returned team_id; the call site dropped it."""
    from app.intake import routing

    src = inspect.getsource(routing)
    assert "w.team = pick.team_id" in src
    assert "request.routed_team_id = w.team" in src


def test_working_state_can_carry_a_team():
    """_WS had no field to hold the team even if you wanted to."""
    from app.intake.routing import _WS

    assert "team" in _WS.__dataclass_fields__


def test_pool_pick_still_carries_both_the_team_and_the_person():
    from app.intake.teams import PoolPick

    assert "team_id" in PoolPick.__dataclass_fields__
    assert "user_id" in PoolPick.__dataclass_fields__


def test_the_team_is_exposed_on_the_api():
    """A column nothing can read would not answer the question either."""
    from app.intake.schemas import RequestResponse

    assert "routed_team_id" in RequestResponse.model_fields
    assert "routed_team_label" in RequestResponse.model_fields

    from app.intake import service

    assert '"routed_team_id": r.routed_team_id' in inspect.getsource(service)


def test_a_human_reassignment_does_not_forge_a_team():
    """Reassigning to a person who was never in the pool must not claim they
    are. Only pick_from_pool sets the team."""
    from app.intake import routing

    src = inspect.getsource(routing)
    assert src.count("w.team = ") == 1


# ==========================================================================
# 3.6 — one department vocabulary
# ==========================================================================

def test_the_hardcoded_frontend_department_list_is_gone():
    """It named Product/Sales/Marketing while the access model reasons about
    Legal & IP and Generics - North America, so an intake department could never
    match a routing rule built from the canonical list."""
    intake_page = (FRONTEND / "app" / "(app)" / "intake" / "page.tsx").read_text()
    assert "const DEPARTMENTS = [" not in intake_page
    assert 'queryFn: rolesApi.listDepartments' in intake_page


def test_departments_come_from_one_place():
    from app.core.departments import BUSINESS_UNITS, DEPARTMENTS, FUNCTION_JOINS_AT

    assert set(DEPARTMENTS) == set(BUSINESS_UNITS) | set(FUNCTION_JOINS_AT)


def test_the_department_list_is_readable_by_any_authenticated_user():
    """The New Request form needs it. Gating it behind an admin permission would
    have 403'd every ordinary requester the moment RBAC is switched back on."""
    from app.roles import routes

    src = inspect.getsource(routes.list_departments)
    assert "get_current_user" in src
    assert "_ASSIGN" not in src


def test_assigning_a_department_is_still_admin_only():
    """Reading the structure is open; putting someone in it is not."""
    from app.roles import routes

    assert "_ASSIGN" in inspect.getsource(routes.set_user_department)


# ==========================================================================
# What area 3 deliberately did NOT do
# ==========================================================================

def test_intake_team_and_approver_group_are_still_separate():
    """Two membership tables for "a named pool of people" remain.

    Merging them means migrating ApprovalRequest.approver_group_id and the
    grants group-principal lookup — surgery on the approvals engine, which the
    audit found to be the one well-built subsystem. Recorded here so the
    duplication stays visible rather than being quietly forgotten.
    """
    from app.approvals.models import ApproverGroup
    from app.intake.models import IntakeTeam

    assert ApproverGroup.__tablename__ != IntakeTeam.__tablename__
