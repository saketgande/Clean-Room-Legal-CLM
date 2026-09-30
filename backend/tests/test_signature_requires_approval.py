"""WF-01: a contract may leave Approval for Signature only when the current
version's approval chain is fully approved, unless the approval engine itself
moves it with an authorized override."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  (register every mapper)
from app.contracts import lifecycle, stage_triggers
from app.core.enums import ApprovalStatus


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self, contract, statuses):
        self.contract = contract
        self.statuses = statuses

    def scalar(self, _stmt):
        return self.contract

    def scalars(self, _stmt):
        return _Rows(self.statuses)

    def add(self, _obj):
        pass


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(lifecycle, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(lifecycle, "write_timeline_event", lambda db, **kw: None)
    monkeypatch.setattr(stage_triggers, "fire_stage_entry_triggers", lambda db, **kw: None)


def _move(statuses, **kwargs):
    contract = SimpleNamespace(id="c-1", org_id="org-1", lifecycle_stage="approval",
                               current_authoritative_version_id="v-2", updated_by_user_id=None)
    lifecycle.transition_contract_stage(FakeDB(contract, statuses), contract=contract, to_stage="signature",
                                        actor_user_id="u-1", reason="workflow: NDA Fast-Track", **kwargs)
    return contract


def test_signature_with_no_approval_chain_is_refused():
    with pytest.raises(HTTPException) as exc:
        _move([])
    assert exc.value.status_code == 409


def test_signature_with_a_pending_rung_is_refused():
    with pytest.raises(HTTPException):
        _move([ApprovalStatus.APPROVED, ApprovalStatus.PENDING])


def test_signature_after_the_chain_is_approved_is_allowed():
    assert _move([ApprovalStatus.APPROVED, ApprovalStatus.APPROVED]).lifecycle_stage == "signature"


def test_the_approval_engine_override_still_moves_the_contract():
    assert _move([], override=True, override_authorized=True).lifecycle_stage == "signature"
