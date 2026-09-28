"""APP-06: an "all members must approve" rung keeps the requirement it was created
with; editing the group afterwards changes neither how many nor which approvals count."""

from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.approvals import service


class GroupDB:
    def __init__(self, members):
        self.group = SimpleNamespace(members=members)

    def get(self, _model, _key):
        return self.group


def _member(uid):
    return SimpleNamespace(id=uid, org_id="org-1")


def _rung(**metadata):
    return SimpleNamespace(mode="all", approver_group_id="grp-1", org_id="org-1", metadata_json=metadata)


def test_needed_approvals_come_from_the_snapshot_not_current_membership():
    rung = _rung(required_approver_ids=["u-1", "u-2", "u-3"], quorum_needed=3)
    shrunk_group = GroupDB([_member("u-1")])  # an admin removed two members after submit
    assert service._quorum_needed(shrunk_group, rung) == 3


def test_only_snapshotted_approvers_count_toward_the_rung():
    rung = _rung(required_approver_ids=["u-1", "u-2"], quorum_needed=2)
    prior = [SimpleNamespace(approver_user_id="u-1", decision="approve")]
    assert service._approvals_toward_quorum(rung, prior, "u-outsider") == 1
    assert service._approvals_toward_quorum(rung, prior, "u-2") == 2


def test_rungs_created_before_the_snapshot_still_use_membership():
    legacy = _rung()
    assert service._quorum_needed(GroupDB([_member("u-1"), _member("u-2")]), legacy) == 2
