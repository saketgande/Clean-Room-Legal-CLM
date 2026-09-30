"""A workflow's Approval step decides who approves — nothing overrides it.

Approval routing rules used to win over the step: the team picked on the step
was only used when no rule matched, so a catch-all rule made it dead, and the
step's "Specific person" was never read at all (the designer saves it as
``assignee_user_id``; approvals read ``approver_user_id``). Automatic gates
also added approvers. Routing rules and gates are gone; these guard the step
being the only answer.
"""

from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.approvals.service import plan_chain
from app.authority.service import _grant_covers
from app.intake.approval_bridge import IntakeApprovalSubject
from app.workflows.service import approval_target

_REQ = SimpleNamespace(requester_user_id="u-req")


class DB:
    """Every team lookup finds the Executive team of this org."""

    def get(self, _model, key):
        return SimpleNamespace(id=key, org_id="o")


def test_a_specific_person_on_the_step_is_the_approver():
    cfg = {"assign_by": "Specific person", "assignee_user_id": "u-gc", "approver_role": "Legal & IP"}
    assert approval_target(DB(), org_id="o", cfg=cfg, request=_REQ)["approver_user_id"] == "u-gc"


def test_the_requester_option_names_the_requester():
    cfg = {"assign_by": "The requester"}
    assert approval_target(DB(), org_id="o", cfg=cfg, request=_REQ)["approver_user_id"] == "u-req"


def test_the_steps_team_is_its_approver():
    target = approval_target(DB(), org_id="o", cfg={"team_id": "team-exec"}, request=_REQ)
    assert target == {"approver_user_id": None, "approver_team_id": "team-exec", "approver_role": None,
                      "mode": "any"}


def test_all_members_must_approve_when_the_step_says_so():
    cfg = {"team_id": "team-exec", "assign_by": "All members must approve"}
    assert approval_target(DB(), org_id="o", cfg=cfg, request=_REQ)["mode"] == "all"


def test_a_team_from_another_org_is_not_used():
    """A copied workflow must not reach into another org's team."""
    class Other:
        def get(self, _model, key):
            return SimpleNamespace(id=key, org_id="elsewhere")

    assert approval_target(Other(), org_id="o", cfg={"team_id": "t"}, request=_REQ)["approver_team_id"] is None


def test_the_named_approver_is_the_only_rung():
    """Nothing adds approvers behind the step's back — no routes, no gates."""
    chain = plan_chain(approver_team_id="team-exec")
    assert [(t["step_order"], t["approver_team_id"]) for t in chain] == [(1, "team-exec")]


def test_a_step_with_no_approver_is_not_silently_skipped():
    """An empty target is kept so submit reports "no approver configured"."""
    [target] = plan_chain()
    assert not any(target[k] for k in ("approver_user_id", "approver_team_id", "approver_role"))


def test_no_authority_limit_covers_an_unknown_value():
    """Delegation of Authority: a missing value used to count as 0."""
    req = SimpleNamespace(field_values={"request_form": "new_agreement"}, priority="Medium",
                          type_label="New agreement Request", legal_entity_id=None)
    grant = SimpleNamespace(max_value=1_000_000, currency=None, allowed_contract_types=[],
                            allowed_jurisdictions=[], max_risk_band=None)
    ok, why = _grant_covers(grant, IntakeApprovalSubject(req, type_key=None))
    assert not ok and "unknown" in why
