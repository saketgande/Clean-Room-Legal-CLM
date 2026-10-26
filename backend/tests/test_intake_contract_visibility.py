"""Request payloads and the contract they are tied to (011 follow-up, defect D1).

Every request payload used to carry contract_id / contract_title even when the viewer was walled
off from that contract (the contract page itself said 404). Each payload must now read as "no
contract" for such a viewer. Runs in one rolled-back transaction in an organisation of its own.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import event, func, select

from app.contracts.models import Contract
from app.intake.service import IntakeService
from tests import test_request_counterparty as base
from tests.test_request_counterparty import World

db, w, api = base.db, base.w, base.api  # the shared rolled-back world and API fixtures


def _tied(w: World, title: str = "Secret Initech deal"):
    """A request filed and assigned to staff and tied to a staff-owned contract."""
    c = w.contract(title=title)
    r = w.request(requester=w.staff, contract=c, assigned_to_user_id=w.staff.id)
    return c, r


def _payloads(w: World, api, user, r) -> dict:
    svc = IntakeService(w.db)
    return {
        "detail": api(user, r.id, "").text,
        "list": api(user, "", "").text,
        "mine": api(user, "", "/mine").text,
        "pool": json.dumps(svc.list_pool_requests(user=user)),
        "my_work": json.dumps(svc.my_work(user=user)),
    }


def test_walled_viewer_gets_no_contract_on_every_payload_but_a_clear_viewer_does(w, api):
    """The defect: the walled viewer's detail, list, mine, pool and my-work all named the contract
    (id and title) that the contract page refuses them."""
    c, r = _tied(w)
    for name, text in _payloads(w, api, w.staff, r).items():
        assert c.id in text and c.title in text, name  # clear viewer: shown as before

    w.wall(c, w.staff)
    detail = api(w.staff, r.id, "").json()
    assert detail["contract_id"] is None and detail["contract_title"] is None
    for name, text in _payloads(w, api, w.staff, r).items():
        assert c.id not in text and c.title not in text, name
    assert r.id in _payloads(w, api, w.staff, r)["list"]  # the request itself is still there


def test_viewer_without_contract_read_or_access_gets_null(w, api):
    """The defect: only the wall was thought of; a viewer with no contract:read, or who is not
    owner/creator/granted, must not learn the contract either."""
    c, r = _tied(w)
    no_read = w.user("No Read", {"intake:create", "intake:read"})
    stranger = w.user("Stranger", {"intake:create", "intake:read", "contract:read"})
    for u in (no_read, stranger):
        body = api(u, r.id, "").json()
        assert body["contract_id"] is None and body["contract_title"] is None
        assert c.title not in json.dumps(body)


def test_request_with_no_contract_is_unchanged(w, api):
    """The defect to avoid: nulling must not disturb requests that never had a contract."""
    r = w.request()
    body = api(w.staff, r.id, "").json()
    assert body["contract_id"] is None and body["contract_title"] is None
    assert body["id"] == r.id


def test_requester_viewing_own_request_cannot_see_contract_they_cannot_open(w, api):
    """The defect: get_request lets a plain requester in; the contract they own nothing of
    must stay hidden from them."""
    c = w.contract()
    r = w.request(contract=c)
    body = api(w.requester, r.id, "").json()
    assert body["contract_id"] is None and c.id not in json.dumps(body)


def test_list_query_count_does_not_grow_with_requests(w, db):
    """The defect to avoid: a per-row access check turning the queue into an N+1."""
    def count(n: int) -> int:
        for _ in range(n):
            _tied(w)
        db.expire_all()
        seen = []
        evt = lambda *a: seen.append(1)
        event.listen(db.get_bind(), "before_cursor_execute", evt)
        try:
            IntakeService(db).list_requests(user=w.staff)
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", evt)
        return len(seen)

    few = count(2)
    many = count(8)
    assert many <= few + 1, (few, many)


# --- approvers and step assignees (correction to D1) --------------------------------------------


def _approver_of(w: World, c, user) -> None:
    from app.approvals.models import ApprovalRequest

    w.db.add(ApprovalRequest(org_id=w.org_id, contract_id=c.id, status="pending",
                             requested_by_user_id=w.staff.id, approver_user_id=user.id))
    w.db.flush()


def _assignee_of(w: World, r, c, user) -> None:
    from app.workflows.models import WorkflowRun, WorkflowStepRun

    run = WorkflowRun(org_id=w.org_id, request_id=r.id, flow_name="f", steps=[], contract_id=c.id)
    w.db.add(run)
    w.db.flush()
    w.db.add(WorkflowStepRun(org_id=w.org_id, flow_run_id=run.id, idx=0, step_type="human_task",
                             step_name="Legal Review", assignee_user_id=user.id))
    w.db.flush()


def _other(w: World, name: str = "Other"):
    return w.user(name, {"intake:create", "intake:read", "contract:read"})


def _sees(w, api, user, r, c) -> bool:
    detail = api(user, r.id, "").json()
    listed = c.title in api(user, "", "").text
    assert (detail["contract_id"] == c.id) == (detail["contract_title"] == c.title) == listed
    return listed


def test_pending_approver_who_is_not_owner_sees_the_contract(w, api):
    """The regression: the contract page admits a pending approver but the first fix nulled the
    contract for them, so the people who must work on it lost the review and editor link."""
    c, r = _tied(w)
    u = _other(w)
    assert not _sees(w, api, u, r, c)
    _approver_of(w, c, u)
    assert _sees(w, api, u, r, c)


def test_workflow_step_assignee_sees_the_contract(w, api):
    """The regression: a human_task reviewer is admitted by the contract page and must be here."""
    c, r = _tied(w)
    u = _other(w)
    _assignee_of(w, r, c, u)
    assert _sees(w, api, u, r, c)


def test_walled_pending_approver_still_gets_null(w, api):
    """The failure mode: approver status must never beat an ethical wall (deny overrides)."""
    c, r = _tied(w)
    u = _other(w)
    _approver_of(w, c, u)
    _assignee_of(w, r, c, u)
    w.wall(c, u)
    assert not _sees(w, api, u, r, c)


def test_under_cleared_approver_gets_null(w, api):
    """The failure mode: an approver below the contract's classification must not learn it."""
    c = w.contract(title="Restricted deal", confidentiality="restricted")
    r = w.request(requester=w.staff, contract=c)
    u = _other(w)
    u.clearance = "internal"
    _approver_of(w, c, u)
    w.db.flush()
    assert not _sees(w, api, u, r, c)


def test_approver_without_contract_read_gets_null(w, api):
    """The failure mode: the permission gate applies before any approver door."""
    c, r = _tied(w)
    u = w.user("NoRead", {"intake:create", "intake:read"})
    _approver_of(w, c, u)
    assert not _sees(w, api, u, r, c)


def test_owner_creator_admin_and_granted_still_see_it(w, api):
    """Guards the batched path against the new fallback replacing it."""
    c, r = _tied(w)
    creator = _other(w, "Creator")
    c2 = w.contract(title="Made by creator", created_by_user_id=creator.id,
                    owner=w.requester)
    r2 = w.request(requester=w.staff, contract=c2)
    assert _sees(w, api, w.staff, r, c)
    assert _sees(w, api, creator, r2, c2)


@pytest.mark.parametrize("path", ["draft-contract", "ingest-attachment"])
def test_draft_and_ingest_refuse_when_contract_is_not_openable(w, db, client, path):
    """The failure mode: the actions are idempotent, so for a viewer who cannot open the request's
    contract they must neither make a second contract nor answer with the first one's id/title."""
    from app.core.deps import get_current_user, get_db
    from app.main import app

    c, r = _tied(w)
    n = lambda: db.scalar(select(func.count()).select_from(Contract).where(
        Contract.org_id == w.org_id))
    before = n()
    u = _other(w)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: u
    res = client.post(f"/api/v1/intake/requests/{r.id}/{path}")
    assert res.status_code in (403, 404), res.text  # 403 = screen gate, 404 = contract refusal
    assert c.id not in res.text and c.title not in res.text
    assert n() == before


def test_draft_contract_refusal_is_404_for_an_editor(w, db, client, monkeypatch):
    """The failure mode: an editor without access reached the idempotent branch. Screen gate is
    stubbed open so the refusal under test is the contract one."""
    from app.core import screen_access
    from app.core.deps import get_current_user, get_db
    from app.main import app

    monkeypatch.setattr(screen_access, "assert_screen_level", lambda *a, **k: None)
    c, r = _tied(w)
    u = _other(w)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: u
    for path in ("draft-contract", "ingest-attachment"):
        res = client.post(f"/api/v1/intake/requests/{r.id}/{path}")
        assert res.status_code == 404 and res.json()["detail"] == "Contract not found"
        assert c.id not in res.text and c.title not in res.text


def test_query_count_is_bounded_with_non_owner_contracts(w, db):
    """The defect to avoid: the approver fallback growing without bound. Per non-owner contract
    it is a constant (sealed check, approver, assignee); the common path stays one query."""
    u = _other(w)
    for _ in range(4):
        c = w.contract()
        w.request(requester=w.staff, contract=c)
    db.expire_all()
    seen = []
    evt = lambda *a: seen.append(1)
    event.listen(db.get_bind(), "before_cursor_execute", evt)
    try:
        IntakeService(db).list_requests(user=u)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", evt)
    assert len(seen) <= 30, len(seen)
