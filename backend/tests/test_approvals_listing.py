"""What the Approvals list tells the page about each row.

* "Yours to action" must not count rows that are already decided: the server's
  ``can_decide`` used to say who is *eligible*, and an administrator is eligible for
  every row, so approved and rejected rows inflated the header count (6 against 3).
* A row that hangs off an intake request (no contract) has to say which request, instead of
  the page printing "No contract linked" beside live Approve buttons.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.approvals import routes


def _row(**overrides):
    fields = dict(
        id="a-1", org_id="org-1", contract_id=None, intake_request_id=None, contract_version_id=None,
        status="pending", requested_by_user_id="u-req", approver_user_id="u-appr", approver_role=None,
        approver_team_id=None, step_order=1, mode="any", metadata_json={},
        due_at=datetime.now(UTC) + timedelta(days=3), created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    return SimpleNamespace(**{**fields, **overrides})


SVC = SimpleNamespace(_quorum_needed=lambda req: 1)


def _serialize(req, **kw):
    kw.setdefault("can_decide", True)
    return routes._serialize_approval(req, approvals_service=SVC, **kw)


@pytest.mark.parametrize("status", ["approved", "rejected", "cancelled", "waiting"])
def test_a_row_that_is_not_pending_cannot_be_decided(status):
    assert _serialize(_row(status=status, contract_id="c-1"), can_decide=True)["can_decide"] is False


def test_a_pending_row_keeps_the_servers_verdict():
    assert _serialize(_row(status="pending", contract_id="c-1"), can_decide=True)["can_decide"] is True
    assert _serialize(_row(status="pending", contract_id="c-1"), can_decide=False)["can_decide"] is False


def test_a_row_that_points_at_nothing_cannot_be_decided():
    assert _serialize(_row(status="pending"), can_decide=True)["can_decide"] is False
    assert _serialize(_row(status="pending", intake_request_id="ir-1"), can_decide=True)["can_decide"] is True


def test_a_row_for_an_intake_request_names_the_request():
    row = _row(intake_request_id="ir-1")
    intake = {"ir-1": SimpleNamespace(id="ir-1", ref="REQ-4123", subject="Notice from the tax office",
                                      description="x", type_label="Notice")}
    out = _serialize(row, intake_requests=intake)
    assert out["intake_request_id"] == "ir-1"
    assert out["intake_ref"] == "REQ-4123"
    assert out["intake_title"] == "Notice from the tax office"


def test_an_intake_row_without_a_subject_falls_back_to_the_first_line_of_the_description():
    row = _row(intake_request_id="ir-1")
    intake = {"ir-1": SimpleNamespace(id="ir-1", ref="REQ-9", subject=None,
                                      description="Employment dispute\nmore detail", type_label="Dispute")}
    assert _serialize(row, intake_requests=intake)["intake_title"] == "Employment dispute"


def test_a_contract_row_has_no_intake_fields():
    out = _serialize(_row(contract_id="c-1"))
    assert out["intake_request_id"] is None and out["intake_ref"] is None and out["intake_title"] is None


def test_overdue_still_reflects_the_clock_for_pending_rows_only():
    past = datetime.now(UTC) - timedelta(hours=3)
    assert _serialize(_row(due_at=past))["overdue"] is True
    assert _serialize(_row(due_at=past, status="approved"))["overdue"] is False


def test_a_row_assigned_to_a_person_carries_their_name():
    # The queue used to look the name up in the users list, which an ordinary approver may not read,
    # so they saw "Assigned approver" instead of who it was with.
    out = _serialize(_row(approver_user_id="u-appr"), user_names={"u-appr": "Gale Approver"})
    assert out["approver_user_name"] == "Gale Approver"


def test_a_row_with_no_person_or_an_unknown_one_has_no_name():
    assert _serialize(_row(approver_user_id=None))["approver_user_name"] is None
    assert _serialize(_row(approver_user_id="u-gone"), user_names={})["approver_user_name"] is None


# ---- who sees a row ---------------------------------------------------------------------------------------

def test_an_approver_keeps_seeing_what_was_assigned_to_them_after_the_contract_moves_on(monkeypatch):
    # Once Finance approved, the contract left the Approval stage; the list then dropped the row for the
    # approver (they can't open the contract), so they could no longer see what they had signed off.
    monkeypatch.setattr(routes, "_user_eligible_to_decide", lambda db, *, approval, user: True)
    monkeypatch.setattr(routes, "user_can_access_contract", lambda db, *, contract, user: False)
    row = _row(contract_id="c-1", status="approved")
    contracts = {"c-1": SimpleNamespace(id="c-1", lifecycle_stage="signature")}
    assert routes._can_view_approval(None, approval=row, user=SimpleNamespace(id="u-fin"), contracts=contracts) is True


def test_someone_it_was_not_assigned_to_and_who_cannot_open_the_contract_does_not_see_it(monkeypatch):
    monkeypatch.setattr(routes, "_user_eligible_to_decide", lambda db, *, approval, user: False)
    monkeypatch.setattr(routes, "user_can_access_contract", lambda db, *, contract, user: False)
    row = _row(contract_id="c-1", status="approved")
    contracts = {"c-1": SimpleNamespace(id="c-1", lifecycle_stage="signature")}
    assert routes._can_view_approval(None, approval=row, user=SimpleNamespace(id="u-other"), contracts=contracts) is False
