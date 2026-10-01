"""APP-06: an "all members must approve" rung keeps the requirement it was created
with; editing the team afterwards changes neither how many nor which approvals count."""

from types import SimpleNamespace

import app.models  # noqa: F401  (register every mapper)
from app.approvals import service


def _team(monkeypatch, *uids):
    """The rung's team currently has these members."""
    from app.intake import teams

    monkeypatch.setattr(teams, "member_users",
                        lambda db, team_id, org_id: [SimpleNamespace(id=u, org_id=org_id) for u in uids])


def _rung(**metadata):
    return SimpleNamespace(mode="all", approver_team_id="team-1", org_id="org-1", metadata_json=metadata)


def test_needed_approvals_come_from_the_snapshot_not_current_membership(monkeypatch):
    rung = _rung(required_approver_ids=["u-1", "u-2", "u-3"], quorum_needed=3)
    _team(monkeypatch, "u-1")  # an admin removed two members after submit
    assert service._quorum_needed(None, rung) == 3


def test_only_snapshotted_approvers_count_toward_the_rung():
    rung = _rung(required_approver_ids=["u-1", "u-2"], quorum_needed=2)
    prior = [SimpleNamespace(approver_user_id="u-1", decision="approve")]
    assert service._approvals_toward_quorum(rung, prior, "u-outsider") == 1
    assert service._approvals_toward_quorum(rung, prior, "u-2") == 2


def test_rungs_created_before_the_snapshot_still_use_membership(monkeypatch):
    legacy = _rung()
    _team(monkeypatch, "u-1", "u-2")
    assert service._quorum_needed(None, legacy) == 2
