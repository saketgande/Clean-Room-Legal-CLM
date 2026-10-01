"""APP-07: every approval rung must be decidable when it's submitted: an active user,
a team with members, or a role someone holds. Otherwise submit fails with a clear
configuration error instead of sitting in Approval with nobody able to act."""

from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.approvals import service
from app.core.enums import UserStatus


def _target(user=None, team=None, role=None):
    return {"approver_user_id": user, "approver_team_id": team, "approver_role": role}


def _members(monkeypatch, *uids):
    from app.intake import teams

    monkeypatch.setattr(teams, "member_users",
                        lambda db, team_id, org_id: [SimpleNamespace(id=u) for u in uids])


class DB:
    def __init__(self, obj=None, holders=0):
        self.obj = obj
        self.holders = holders

    def get(self, _model, _key):
        return self.obj

    def scalar(self, _stmt):
        return self.holders


def test_a_team_with_no_members_is_a_configuration_error(monkeypatch):
    _members(monkeypatch)
    finance = SimpleNamespace(id="team-1", name="Finance")
    assert "has no members" in service._rung_config_problem(DB(finance), _target(team="team-1"), "org-1")


def test_a_role_nobody_holds_is_a_configuration_error():
    problem = service._rung_config_problem(DB(holders=0), _target(role="gc"), "org-1")
    assert "holds the 'gc' approver role" in problem


def test_a_rung_with_no_approver_is_a_configuration_error():
    assert "no approver" in service._rung_config_problem(DB(), _target(), "org-1")


def test_resolvable_rungs_pass(monkeypatch):
    _members(monkeypatch, "u-1")
    legal = SimpleNamespace(id="team-1", name="Legal Counsel")
    assert service._rung_config_problem(DB(legal), _target(team="team-1"), "org-1") is None
    assert service._rung_config_problem(DB(holders=2), _target(role="approver"), "org-1") is None
    active = SimpleNamespace(status=UserStatus.ACTIVE)
    assert service._rung_config_problem(DB(active), _target(user="u-1"), "org-1") is None
