"""AUTH-01: the contract row-level gate (ethical walls + clearance) must hold on
every path that reaches a contract, not only the direct contract_id one."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai import tool_runtime as tr
from app.workflows import routes as wf

USER = SimpleNamespace(id="u-1", org_id="org-1")


def _walled(db, *, contract_id, user):
    raise HTTPException(404, "Contract not found")


class FakeDB:
    def __init__(self, obj):
        self.obj = obj

    def get(self, model, key, with_for_update=False):
        return self.obj if key == self.obj.id else None


def _run(contract_id="walled-contract", org_id="org-1"):
    return SimpleNamespace(id="run-1", org_id=org_id, contract_id=contract_id)


def test_intake_request_link_does_not_bypass_contract_access(monkeypatch):
    monkeypatch.setattr(tr, "get_contract_for_user", _walled)
    monkeypatch.setattr(
        tr.tool_runtime, "_resolve_request",
        lambda db, ref, user: SimpleNamespace(id="req-1", contract_id="walled-contract"),
    )
    with pytest.raises(HTTPException) as exc:
        tr.tool_runtime._resolve_contract_id(None, request_id="REQ-4188", contract_id=None, user=USER)
    assert exc.value.status_code == 404


def test_run_routes_enforce_contract_access(monkeypatch):
    monkeypatch.setattr(wf, "get_contract_for_user", _walled)
    with pytest.raises(HTTPException):
        wf._get_run(FakeDB(_run()), USER, "run-1")


def test_run_without_a_contract_needs_only_the_org(monkeypatch):
    monkeypatch.setattr(wf, "get_contract_for_user", _walled)
    run = _run(contract_id=None)
    assert wf._get_run(FakeDB(run), USER, "run-1") is run


def test_run_from_another_org_is_hidden():
    with pytest.raises(HTTPException):
        wf._get_run(FakeDB(_run(contract_id=None, org_id="org-2")), USER, "run-1")


def test_run_by_request_enforces_contract_access(monkeypatch):
    monkeypatch.setattr(wf, "get_contract_for_user", _walled)
    monkeypatch.setattr(wf.service, "get_run_for_request", lambda db, **kw: _run())
    with pytest.raises(HTTPException):
        wf.run_for_request("req-1", db=None, current_user=USER)


def test_start_refuses_a_request_linked_to_a_walled_contract(monkeypatch):
    monkeypatch.setattr(wf, "get_contract_for_user", _walled)
    started = []

    async def fake_start_flow(*args, **kwargs):
        started.append(True)

    monkeypatch.setattr(wf.service, "start_flow", fake_start_flow)
    request = SimpleNamespace(id="req-1", org_id="org-1", contract_id="walled-contract")
    with pytest.raises(HTTPException):
        http_request = SimpleNamespace(state=SimpleNamespace(request_id="rid-1"))
        asyncio.run(wf.start(wf.StartPayload(request_id="req-1"), http_request, db=FakeDB(request), current_user=USER))
    assert started == []


def test_ask_aegis_cannot_start_a_workflow_on_a_walled_contract(monkeypatch):
    import app.workflows.service as wf_service

    monkeypatch.setattr(tr, "get_contract_for_user", _walled)
    monkeypatch.setattr(
        tr.tool_runtime, "_resolve_request",
        lambda db, ref, user: SimpleNamespace(id="req-1", contract_id="walled-contract"),
    )
    started = []

    async def fake_start_flow(*args, **kwargs):
        started.append(True)

    monkeypatch.setattr(wf_service, "start_flow", fake_start_flow)
    payload = SimpleNamespace(request_id="REQ-1", workflow_id=None)
    with pytest.raises(HTTPException):
        asyncio.run(tr.tool_runtime._start_intake_workflow(None, payload=payload, user=USER))
    assert started == []


def test_ask_aegis_cannot_advance_a_workflow_on_a_walled_contract(monkeypatch):
    monkeypatch.setattr(tr, "get_contract_for_user", _walled)
    monkeypatch.setattr(
        tr.tool_runtime, "_resolve_request",
        lambda db, ref, user: SimpleNamespace(id="req-1", contract_id=None),
    )
    monkeypatch.setattr(tr.tool_runtime, "_latest_run", lambda db, request_id: _run())
    payload = SimpleNamespace(request_id="REQ-1", note=None)
    with pytest.raises(HTTPException):
        asyncio.run(tr.tool_runtime._advance_intake_workflow(None, payload=payload, user=USER))
