"""AUTH-03: the function that hands a contract's text to an AI skill takes the user,
so ethical walls and clearance decide what it returns, not the tenant alone."""

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.ai.context import build_contract_context
from app.ai.controller import ai_controller
from app.contracts import access
from app.contracts.models import Contract


class _DB:
    def get(self, model, _key):
        if model is Contract:
            return Contract(id="c-1", org_id="org-1", title="MSA", owner_user_id="u-owner")
        return None


def test_every_caller_must_name_the_user():
    assert inspect.signature(build_contract_context).parameters["user"].default is inspect.Parameter.empty
    for fn in (ai_controller._maybe_contract_context, ai_controller._contract_context_summaries):
        assert inspect.signature(fn).parameters["user"].default is inspect.Parameter.empty


def test_a_walled_user_gets_nothing(monkeypatch):
    monkeypatch.setattr(access, "user_can_access_contract", lambda db, *, contract, user: False)
    with pytest.raises(HTTPException) as exc:
        build_contract_context(_DB(), user=SimpleNamespace(id="u-walled", org_id="org-1"),
                               org_id="org-1", contract_id="c-1")
    assert exc.value.status_code == 404


def test_a_user_who_may_open_it_gets_the_context(monkeypatch):
    monkeypatch.setattr(access, "user_can_access_contract", lambda db, *, contract, user: True)
    ctx = build_contract_context(_DB(), user=SimpleNamespace(id="u-ok", org_id="org-1"),
                                 org_id="org-1", contract_id="c-1")
    assert ctx.contract.id == "c-1"


def test_a_background_job_runs_without_a_live_actor():
    assert "None if job_id else" in inspect.getsource(ai_controller.run_structured_skill)
    ctx = build_contract_context(_DB(), user=None, org_id="org-1", contract_id="c-1")  # no user, no access check
    assert ctx.contract.id == "c-1"
