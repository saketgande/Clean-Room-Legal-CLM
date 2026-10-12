"""WF-01 (workflow side): a signature step must never walk a contract THROUGH
Approval. It leaves Approval only once the current version is fully approved; a
flow without an approval step takes the direct review → signature edge instead."""

from types import SimpleNamespace

import pytest

from app.contracts import lifecycle
from app.workflows import service

ACTOR = SimpleNamespace(id="u-1")


@pytest.fixture
def moves(monkeypatch):
    calls = []

    def fake_transition(db, *, contract, to_stage, actor_user_id, reason):
        calls.append((contract.lifecycle_stage, to_stage))
        contract.lifecycle_stage = to_stage

    monkeypatch.setattr(lifecycle, "transition_contract_stage", fake_transition)
    return calls


def test_signature_step_from_review_skips_approval_explicitly(moves):
    contract = SimpleNamespace(lifecycle_stage="review")
    service._advance_contract_to(None, contract=contract, target="signature", actor=ACTOR, reason="workflow: NDA")
    assert moves == [("review", "signature")]


def test_contract_waiting_for_its_approval_stays_in_approval(moves, monkeypatch):
    monkeypatch.setattr(lifecycle, "_current_version_approved", lambda db, contract: False)
    contract = SimpleNamespace(lifecycle_stage="approval")
    service._advance_contract_to(None, contract=contract, target="signature", actor=ACTOR, reason="workflow: MSA")
    assert moves == []
    assert contract.lifecycle_stage == "approval"


def test_approval_step_still_enters_approval(moves):
    contract = SimpleNamespace(lifecycle_stage="drafting")
    service._advance_contract_to(None, contract=contract, target="approval", actor=ACTOR, reason="workflow: MSA")
    assert moves == [("drafting", "review"), ("review", "approval")]


def test_an_approved_contract_moves_from_approval_to_signature(moves, monkeypatch):
    """WF-06: with the chain approved, the workflow's signature step (after any
    negotiation) is what moves the contract into signing."""
    monkeypatch.setattr(lifecycle, "_current_version_approved", lambda db, contract: True)
    contract = SimpleNamespace(lifecycle_stage="approval")
    service._advance_contract_to(None, contract=contract, target="signature", actor=ACTOR, reason="workflow: MSA")
    assert moves == [("approval", "signature")]
