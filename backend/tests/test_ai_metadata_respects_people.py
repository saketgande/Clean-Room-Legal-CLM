"""LLM-04: AI-extracted metadata is a suggestion. It may fill a blank or refresh a
value the AI wrote itself, but never overwrite what a person entered, and it never
sets risk_level (owned by the weighted risk score)."""

from types import SimpleNamespace

import pytest

import app.ai.controller as controller_module
import app.contracts.service as contracts_service
from app.ai.controller import ai_controller
from app.ai.schemas import CitationInput, ContractMetadataOutput


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(controller_module, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(controller_module, "write_timeline_event", lambda db, **kw: None)
    monkeypatch.setattr(contracts_service, "write_audit_log", lambda db, **kw: None)
    monkeypatch.setattr(contracts_service, "write_timeline_event", lambda db, **kw: None)


def _contract(**fields):
    base = dict(id="c-1", org_id="org-1", title="MSA", confidentiality=None, contract_type=None, counterparty_name=None,
                jurisdiction=None, risk_level=None, value_amount=None, currency=None, effective_date=None,
                expiration_date=None, metadata_json={}, updated_by_user_id=None)
    base.update(fields)
    return SimpleNamespace(**base)


def _extract(contract, **values):
    output = ContractMetadataOutput(confidence="high", citations=[CitationInput(quote="by and between")], **values)
    ai_controller._persist_metadata(None, output=output, context=SimpleNamespace(contract=contract),
                                    created_by_user_id="u-1", request_id=None, skill_run=SimpleNamespace(id="run-1"))


def test_ai_never_overwrites_a_value_a_person_entered():
    contract = _contract(counterparty_name="Acme Corp", risk_level="high")
    _extract(contract, counterparty_name="ACME Holdings LLC", contract_type="MSA", risk_level="low")
    assert contract.counterparty_name == "Acme Corp"
    assert contract.risk_level == "high"
    assert contract.contract_type == "MSA"
    assert contract.metadata_json["field_sources"] == {"contract_type": "ai"}
    assert contract.metadata_json["ai_suggestions"]["metadata"]["counterparty_name"] == "ACME Holdings LLC"


def test_ai_refreshes_a_value_it_wrote_itself():
    contract = _contract(contract_type="MSA", metadata_json={"field_sources": {"contract_type": "ai"}})
    _extract(contract, contract_type="Master Services Agreement")
    assert contract.contract_type == "Master Services Agreement"


class _NoopDB:
    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def test_a_person_editing_an_ai_filled_field_takes_ownership_of_it():
    contract = _contract(contract_type="MSA", metadata_json={"field_sources": {"contract_type": "ai"}})
    contracts_service.update_contract_metadata(
        _NoopDB(), contract=contract, user=SimpleNamespace(id="u-2", org_id="org-1"),
        updates={"contract_type": "Services Agreement"},
    )
    assert contract.metadata_json["field_sources"]["contract_type"] == "user"
    _extract(contract, contract_type="MSA")
    assert contract.contract_type == "Services Agreement"
