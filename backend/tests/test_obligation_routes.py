"""APP-01: the single-obligation routes (get / update / complete) crashed with
AttributeError because _get_obligation returned the serialized dict instead of
the ORM row the callers mutate."""

from types import SimpleNamespace

from app.contracts.models import Contract
from app.obligations import routes
from app.obligations.models import Obligation


class FakeDB:
    def __init__(self, ob):
        self.ob = ob
        self.commits = 0

    def get(self, model, key):
        if model is Obligation and key == self.ob.id:
            return self.ob
        if model is Contract and key == self.ob.contract_id:
            return SimpleNamespace(title="Master Services Agreement", counterparty_name="Acme")
        return None

    def commit(self):
        self.commits += 1

    def refresh(self, obj, **kwargs):
        pass


def _setup(monkeypatch):
    ob = Obligation(id="ob-1", org_id="org-1", contract_id="c-1", description="Pay invoice", status="open")
    monkeypatch.setattr(routes, "get_contract_for_user", lambda db, **kw: None)
    monkeypatch.setattr(routes, "write_audit_log", lambda db, **kw: None)
    return ob, FakeDB(ob), SimpleNamespace(id="u-1", org_id="org-1")


def test_get_obligation_helper_returns_the_row(monkeypatch):
    ob, db, user = _setup(monkeypatch)
    assert routes._get_obligation(db, obligation_id="ob-1", current_user=user) is ob


def test_get_obligation_route_serializes(monkeypatch):
    _ob, db, user = _setup(monkeypatch)
    body = routes.get_obligation("ob-1", db=db, current_user=user)
    assert body["id"] == "ob-1"
    assert body["contract_title"] == "Master Services Agreement"


def test_complete_obligation_marks_completed(monkeypatch):
    ob, db, user = _setup(monkeypatch)
    body = routes.complete_obligation("ob-1", db=db, current_user=user)
    assert ob.status == "completed"
    assert ob.updated_by_user_id == "u-1"
    assert body["status"] == "completed"
    assert db.commits == 1


def test_update_obligation_applies_fields(monkeypatch):
    ob, db, user = _setup(monkeypatch)
    payload = routes.ObligationUpdate(responsible_party="counterparty", status="cancelled")
    body = routes.update_obligation("ob-1", payload, db=db, current_user=user)
    assert ob.responsible_party == "counterparty"
    assert body["status"] == "cancelled"
