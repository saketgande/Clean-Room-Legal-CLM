"""Departmental access — Dr. Reddy's structure driving who sees a contract.

Two rules, on top of the single access check in app/core/policy.py:

  1. the BUSINESS UNIT that owns a deal works it for the contract's whole life;
  2. a reviewing FUNCTION joins at a lifecycle stage and keeps access from then
     on (cumulative), at the level set in app/core/departments.py.

The last test in this file is the important one: the SQL used for list pages and
the Python used for the detail view must allow exactly the same contracts. Those
two drifting apart is the original disease this whole effort is treating.

House style: in-memory models, a tiny stub session, no database.
"""

import pytest

from app.auth.models import Permission, Role, User
from app.contracts.models import Contract
from app.core.departments import (
    BUSINESS_UNITS,
    DEPARTMENTS,
    FUNCTION_JOINS_AT,
    STAGE_ORDER,
    function_level_at,
    stages_visible_to,
)
from app.core.enums import ContractLifecycleStage, UserStatus
from app.core.policy import can, department_level

ORG = "org-1"


def _user(*, department=None, user_id="user-1", role="member", clearance="restricted") -> User:
    r = Role(id=role, org_id=ORG, name=role)
    r.permissions = [Permission(value="contract:read")]
    u = User(
        id=user_id, org_id=ORG, email=f"{user_id}@drreddys.com", full_name="Test User",
        hashed_password="hash", status=UserStatus.ACTIVE, clearance=clearance,
        department=department,
    )
    u.roles = [r]
    return u


def _contract(*, stage=ContractLifecycleStage.REVIEW, business_unit=None) -> Contract:
    return Contract(
        id="contract-1", org_id=ORG, title="API Supply Agreement",
        owner_user_id="owner-9", created_by_user_id="creator-9",
        confidentiality="internal", lifecycle_stage=stage, business_unit=business_unit,
    )


class _StubDB:
    """No grants, no walls, no matter membership — isolates the department rule."""

    def scalar(self, *_a, **_kw):
        return None

    def scalars(self, *_a, **_kw):
        class _Empty:
            def all(self_inner):
                return []

        return _Empty()


@pytest.fixture(autouse=True)
def no_other_layers(monkeypatch):
    monkeypatch.setattr("app.core.policy.user_is_walled", lambda db, *, user, contract: False)
    monkeypatch.setattr(
        "app.core.policy.user_has_grant",
        lambda db, *, user, resource_type, resource_id, min_level="read": False,
    )
    monkeypatch.setattr("app.core.policy._contract_project_level", lambda db, *, user, contract: None)
    monkeypatch.setattr("app.core.policy._is_pending_approver", lambda db, *, contract, user: False)
    monkeypatch.setattr("app.contracts.access._log_deny_override", lambda *a, **kw: None)


# ==========================================================================
# The structure itself
# ==========================================================================

def test_dr_reddys_business_units():
    assert BUSINESS_UNITS == [
        "Generics - North America",
        "Generics - Europe",
        "Generics - India",
        "Generics - Emerging Markets",
        "PSAI / CDMO",
        "Proprietary Products",
        "Aurigene",
    ]


def test_reviewing_functions_cover_the_contract_touching_departments():
    assert set(FUNCTION_JOINS_AT) == {
        "Legal & IP",
        "R&D / Clinical",
        "Procurement",
        "Quality & Compliance",
        "Regulatory Affairs",
        "Finance & Tax",
        "Risk & Compliance",
        "Supply Chain",
        "IT / Digital",
        "HR",
    }


def test_stage_order_matches_the_contract_lifecycle():
    """Must stay in step with flows/service.py:_STAGE_ORDER, or a department
    would join at a stage the contract never reaches."""
    assert STAGE_ORDER == [
        "intake", "drafting", "review", "approval", "signature", "active", "closed"
    ]


def test_every_department_name_is_unique():
    assert len(DEPARTMENTS) == len(set(DEPARTMENTS))


# ==========================================================================
# Rule 1 — the owning business unit
# ==========================================================================

@pytest.mark.parametrize("stage", STAGE_ORDER)
def test_owning_business_unit_can_work_the_deal_at_every_stage(stage):
    user = _user(department="PSAI / CDMO")
    contract = _contract(stage=stage, business_unit="PSAI / CDMO")
    assert can(_StubDB(), user=user, resource=contract, level="update") is True


def test_a_different_business_unit_sees_nothing():
    """Generics Europe has no business with a PSAI deal."""
    user = _user(department="Generics - Europe")
    contract = _contract(stage=ContractLifecycleStage.ACTIVE, business_unit="PSAI / CDMO")
    assert can(_StubDB(), user=user, resource=contract, level="read") is False


def test_no_department_means_no_standing_access():
    """The pre-migration state: NULL department grants nothing."""
    user = _user(department=None)
    contract = _contract(business_unit="PSAI / CDMO")
    assert can(_StubDB(), user=user, resource=contract, level="read") is False


# ==========================================================================
# Rule 2 — reviewing functions join at a stage, then keep access
# ==========================================================================

@pytest.mark.parametrize(
    "stage,legal_can_read",
    [
        (ContractLifecycleStage.INTAKE, False),
        (ContractLifecycleStage.DRAFTING, False),
        (ContractLifecycleStage.REVIEW, True),      # Legal & IP joins here
        (ContractLifecycleStage.APPROVAL, True),
        (ContractLifecycleStage.SIGNATURE, True),
        (ContractLifecycleStage.ACTIVE, True),
        (ContractLifecycleStage.CLOSED, True),
    ],
)
def test_legal_joins_at_review_and_keeps_access(stage, legal_can_read):
    """Cumulative: the team that negotiated it still answers for it years later."""
    user = _user(department="Legal & IP")
    assert can(_StubDB(), user=user, resource=_contract(stage=stage), level="read") is legal_can_read


def test_quality_and_regulatory_do_not_see_early_drafts():
    """A half-written draft is not a GxP artefact — they join at approval."""
    for dept in ("Quality & Compliance", "Regulatory Affairs"):
        user = _user(department=dept)
        early = _contract(stage=ContractLifecycleStage.DRAFTING)
        assert can(_StubDB(), user=user, resource=early, level="read") is False, dept
        at_approval = _contract(stage=ContractLifecycleStage.APPROVAL)
        assert can(_StubDB(), user=user, resource=at_approval, level="read") is True, dept


def test_finance_joins_at_approval_not_before():
    user = _user(department="Finance & Tax")
    assert can(_StubDB(), user=user, resource=_contract(stage="review"), level="read") is False
    assert can(_StubDB(), user=user, resource=_contract(stage="approval"), level="read") is True


def test_supply_chain_only_sees_live_contracts():
    user = _user(department="Supply Chain")
    assert can(_StubDB(), user=user, resource=_contract(stage="signature"), level="read") is False
    assert can(_StubDB(), user=user, resource=_contract(stage="active"), level="read") is True


def test_hr_and_it_have_no_standing_contract_access():
    """They reach their own paper by ownership or a grant, never by department."""
    for dept in ("HR", "IT / Digital"):
        user = _user(department=dept)
        contract = _contract(stage=ContractLifecycleStage.ACTIVE)
        assert can(_StubDB(), user=user, resource=contract, level="read") is False, dept


# ==========================================================================
# Levels — a reviewer is not an editor
# ==========================================================================

def test_legal_can_edit_but_quality_can_only_comment():
    contract = _contract(stage=ContractLifecycleStage.APPROVAL)

    legal = _user(department="Legal & IP")
    assert can(_StubDB(), user=legal, resource=contract, level="update") is True

    quality = _user(department="Quality & Compliance")
    assert can(_StubDB(), user=quality, resource=contract, level="comment") is True
    assert can(_StubDB(), user=quality, resource=contract, level="update") is False


def test_risk_and_compliance_is_read_only():
    contract = _contract(stage=ContractLifecycleStage.APPROVAL)
    user = _user(department="Risk & Compliance")
    assert can(_StubDB(), user=user, resource=contract, level="read") is True
    assert can(_StubDB(), user=user, resource=contract, level="comment") is False


def test_no_department_can_share_or_own_by_department_alone():
    """Sharing and ownership stay deliberate acts, never a side effect of
    belonging to a department."""
    contract = _contract(stage=ContractLifecycleStage.ACTIVE, business_unit="PSAI / CDMO")
    for dept in ("Legal & IP", "PSAI / CDMO"):
        user = _user(department=dept)
        assert can(_StubDB(), user=user, resource=contract, level="owner") is False, dept


# ==========================================================================
# Deny-overrides still win
# ==========================================================================

def test_a_wall_beats_departmental_access(monkeypatch):
    monkeypatch.setattr("app.core.policy.user_is_walled", lambda db, *, user, contract: True)
    user = _user(department="Legal & IP")
    contract = _contract(stage=ContractLifecycleStage.ACTIVE)
    assert can(_StubDB(), user=user, resource=contract, level="read") is False


def test_clearance_beats_departmental_access():
    """A Proprietary Products molecule stays restricted to cleared staff."""
    user = _user(department="Legal & IP", clearance="internal")
    contract = _contract(stage=ContractLifecycleStage.ACTIVE)
    contract.confidentiality = "restricted"
    assert can(_StubDB(), user=user, resource=contract, level="read") is False


# ==========================================================================
# The one that matters: list SQL == detail check
# ==========================================================================

def test_helper_and_policy_agree_on_every_department_and_stage():
    """``department_level`` (detail view) and ``stages_visible_to`` (list query)
    are two expressions of one rule. Walk every combination and prove they
    cannot disagree — the drift that produced eleven mechanisms in the first
    place."""
    for dept in DEPARTMENTS:
        visible = set(stages_visible_to(dept))
        for stage in STAGE_ORDER:
            contract = _contract(stage=stage)
            row_says = department_level(_user(department=dept), contract) is not None
            sql_says = stage in visible
            # business units are matched by business_unit, not by stage
            if dept in BUSINESS_UNITS:
                assert row_says is False and sql_says is False, (dept, stage)
            else:
                assert row_says == sql_says, (dept, stage)
                assert row_says == (function_level_at(dept, stage) is not None)


def test_business_unit_is_matched_by_unit_not_by_stage():
    for unit in BUSINESS_UNITS:
        user = _user(department=unit)
        owned = _contract(stage=ContractLifecycleStage.INTAKE, business_unit=unit)
        assert department_level(user, owned) == "update", unit
        assert stages_visible_to(unit) == [], unit
