"""F5: the assistant's renewal, obligation and notice tools must hold the same
contract row gate (ethical walls + clearance) the pages hold. Chat is not a side
door around a wall."""

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai import tool_runtime as tr
from app.ai.tool_registry import (
    CompleteObligationInput,
    ListNoticesInput,
    ListRenewalsInput,
    NoticeRef,
)
from app.contracts.models import Contract
from app.core.enums import ObligationStatus
from app.notices import service as notices_service
from app.notices.models import Notice
from app.notices.service import NoticesService
from app.obligations import service as obligations_service
from app.obligations.models import Obligation

USER = SimpleNamespace(id="u-1", org_id="org-1")


def _walled(self, *, contract_id, user):
    raise HTTPException(404, "Contract not found")


class FakeDB:
    """Hands back one row by id and records every statement it is asked to run."""

    def __init__(self, obj=None):
        self.obj, self.statements, self.added = obj, [], []

    def get(self, model, key, **kwargs):
        if self.obj is not None and isinstance(self.obj, model) and key == self.obj.id:
            return self.obj
        if model is Contract:
            return SimpleNamespace(id=key, org_id="org-1", title="MSA", counterparty_name="Acme")
        return None

    def scalar(self, stmt):
        self.statements.append(stmt)
        return self.obj

    def scalars(self, stmt):
        self.statements.append(stmt)
        return SimpleNamespace(all=list)

    def refresh(self, obj, **kwargs):
        pass

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass

    def commit(self):
        pass


def _sql(stmt) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def _obligation(**kw):
    return Obligation(id="ob-1", org_id="org-1", contract_id="c-1", description="Send the report",
                      due_date=date(2026, 3, 10), status=ObligationStatus.OPEN, **kw)


def _notice(contract_id):
    return Notice(id="n-1", org_id="org-1", contract_id=contract_id, subject="Breach notice")


# --- obligations -------------------------------------------------------------

def test_complete_obligation_tool_refuses_walled_contract(monkeypatch):
    """Chat closing a duty on a contract the user is walled from (only org was checked)."""
    from app.contracts.service import ContractService

    monkeypatch.setattr(ContractService, "get_contract_for_user", _walled)
    ob = _obligation()
    with pytest.raises(HTTPException) as exc:
        tr.tool_runtime._complete_obligation(
            FakeDB(ob), payload=CompleteObligationInput(obligation_id="ob-1"), user=USER)
    assert exc.value.status_code == 404
    assert ob.status == ObligationStatus.OPEN


def test_complete_obligation_tool_writes_audit_row(monkeypatch):
    """A chat completion left no obligation.completed compliance record (it skipped the service)."""
    from app.contracts.service import ContractService

    monkeypatch.setattr(ContractService, "get_contract_for_user", lambda self, **kw: None)
    audits = []
    monkeypatch.setattr(obligations_service, "write_audit_log", lambda db, **kw: audits.append(kw["action"]))
    ob = _obligation()
    out = tr.tool_runtime._complete_obligation(
        FakeDB(ob), payload=CompleteObligationInput(obligation_id="ob-1"), user=USER)
    assert audits == ["obligation.completed"]
    assert out["completed"] is True and ob.status == ObligationStatus.COMPLETED


def test_complete_obligation_tool_ignores_deleted_obligation(monkeypatch):
    """Chat completing a soft-deleted obligation the page can no longer see."""
    from app.contracts.service import ContractService

    monkeypatch.setattr(ContractService, "get_contract_for_user", lambda self, **kw: None)
    ob = _obligation(deleted_at=date(2026, 1, 1))
    with pytest.raises(HTTPException) as exc:
        tr.tool_runtime._complete_obligation(
            FakeDB(ob), payload=CompleteObligationInput(obligation_id="ob-1"), user=USER)
    assert exc.value.status_code == 404


# --- renewals ----------------------------------------------------------------

def test_list_renewals_tool_uses_contract_access_filter(monkeypatch):
    """The org-only renewal query coming back, listing walled contracts' renewals in chat."""
    monkeypatch.setattr(tr, "accessible_contract_filter", lambda user: Contract.id == "sentinel-gate")
    db = FakeDB()
    tr.tool_runtime._list_renewals(db, payload=ListRenewalsInput(), user=USER)
    assert "sentinel-gate" in _sql(db.statements[0])


# --- notices -----------------------------------------------------------------

def test_notice_on_walled_contract_is_not_found(monkeypatch):
    """A notice linked to a walled contract still readable / draftable by id (page and chat)."""
    monkeypatch.setattr(notices_service, "is_sealed_off", lambda db, **kw: True)
    db = FakeDB(_notice("c-walled"))
    with pytest.raises(HTTPException) as exc:
        tr.tool_runtime._read_notice(db, payload=NoticeRef(notice_id="n-1"), user=USER)
    assert exc.value.status_code == 404 and exc.value.detail == "Notice not found"
    with pytest.raises(HTTPException) as exc:
        NoticesService(db).draft_response(actor=USER, notice_id="n-1")
    assert exc.value.detail == "Notice not found"


def test_notice_without_contract_needs_only_the_org(monkeypatch):
    """Over-tightening: a notice with no contract must stay visible to the org."""
    monkeypatch.setattr(notices_service, "is_sealed_off", lambda db, **kw: True)
    monkeypatch.setattr(NoticesService, "serialize", lambda self, n, **kw: n)
    notice = _notice(None)
    assert NoticesService(FakeDB(notice)).get_notice(user=USER, notice_id="n-1") is notice


def test_list_notices_filters_by_contract_access(monkeypatch):
    """The notice register (page and chat) listing notices on walled contracts."""
    monkeypatch.setattr(notices_service, "sealed_off_filter", lambda user: Contract.id == "sentinel-gate")
    db = FakeDB()
    tr.tool_runtime._list_notices(db, payload=ListNoticesInput(), user=USER)
    sql = _sql(db.statements[0])
    assert "sentinel-gate" in sql and "contract_id IS NULL" in sql


def test_a_notice_is_kept_from_legal_staff_only_by_a_wall_not_by_ownership(monkeypatch):
    """Legal staff work every notice. Inheriting the contract's ownership rule hid a notice from
    the reviewer assigned to it whenever they did not own the contract; only a deny-override (an
    ethical wall, or clearance) may hide it."""
    monkeypatch.setattr(notices_service, "is_sealed_off", lambda db, **kw: False)
    monkeypatch.setattr(NoticesService, "serialize", lambda self, n, **kw: n)
    notice = _notice("c-owned-by-someone-else")
    assert NoticesService(FakeDB(notice)).get_notice(user=USER, notice_id="n-1") is notice


def test_a_notice_cannot_be_linked_to_a_contract_the_filer_is_walled_from(monkeypatch):
    """Reads were walled but writes were not: create/update could link a notice to a walled
    contract (revealing it exists) and the filer then got 404 on their own notice."""
    monkeypatch.setattr(notices_service, "is_sealed_off", lambda db, **kw: True)

    class _DB:
        def scalar(self, _stmt):
            return Contract(id="c-walled", org_id=USER.org_id)

    with pytest.raises(HTTPException) as exc:
        NoticesService(_DB())._validate_contract(USER, "c-walled")
    assert exc.value.status_code == 404 and exc.value.detail == "Contract not found"
