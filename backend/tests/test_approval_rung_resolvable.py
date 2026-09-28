"""APP-07: every approval rung must be decidable when it's submitted: an active user,
a group with members, or a role someone holds. Otherwise submit fails with a clear
configuration error instead of sitting in Approval with nobody able to act."""

from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.approvals import service
from app.core.enums import UserStatus
from app.workflows import service as workflows


def _target(user=None, group=None, role=None):
    return {"approver_user_id": user, "approver_group_id": group, "approver_role": role}


class DB:
    def __init__(self, obj=None, holders=0):
        self.obj = obj
        self.holders = holders

    def get(self, _model, _key):
        return self.obj

    def scalar(self, _stmt):
        return self.holders


def test_a_group_with_no_members_is_a_configuration_error():
    empty = SimpleNamespace(name="Finance", members=[])
    assert "has no members" in service._rung_config_problem(DB(empty), _target(group="grp-1"), "org-1")


def test_a_role_nobody_holds_is_a_configuration_error():
    problem = service._rung_config_problem(DB(holders=0), _target(role="gc"), "org-1")
    assert "holds the 'gc' approver role" in problem


def test_a_rung_with_no_approver_is_a_configuration_error():
    assert "no approver" in service._rung_config_problem(DB(), _target(), "org-1")


def test_resolvable_rungs_pass():
    legal = SimpleNamespace(name="Legal Counsel", members=[SimpleNamespace(org_id="org-1")])
    assert service._rung_config_problem(DB(legal), _target(group="grp-1"), "org-1") is None
    assert service._rung_config_problem(DB(holders=2), _target(role="approver"), "org-1") is None
    active = SimpleNamespace(status=UserStatus.ACTIVE)
    assert service._rung_config_problem(DB(active), _target(user="u-1"), "org-1") is None


def test_library_role_tokens_map_to_the_seeded_approver_groups():
    assert workflows._FLOW_ROLE_GROUP["finance & tax"] == "Finance"
    assert workflows._FLOW_ROLE_GROUP["gc"] == "Executive"
    unmapped = SimpleNamespace(scalar=lambda _stmt: (_ for _ in ()).throw(AssertionError("no query expected")))
    assert workflows._flow_role_group(unmapped, org_id="org-1", role="unknown-token") is None
