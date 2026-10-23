"""Round 2, bug 6: the "AI review" step of every NDA flow only read the request text.

The step's agent key is "nda", which the engine mapped to the keyword classifier. So the step
recorded source "regex" and a fixed confidence, said "No analysis ran", and waited for a person,
even when the contract already had a finished playbook review with findings. An NDA step with a
contract now reads the contract's playbook review, the same gate an MSA flow uses.
"""

import asyncio
from types import SimpleNamespace

import pytest

import app.models  # noqa: F401  (register every mapper)
from app.contracts import risk
from app.workflows import service


class FakeDB:
    def commit(self):
        pass

    def get(self, _model, _key):
        return SimpleNamespace(id="req-1", type_label="NDA", description="their NDA", field_values={},
                               priority="Medium", ai_triage={}, screening=None)

    def scalar(self, *_a, **_k):
        return None


def _risk(band, counts=None, note=None):
    counts = counts or {"critical": 0, "high": 0, "medium": 0, "low": 0}
    return {"band": band, "counts": counts, "note": note, "drivers": []}


def _run_step(monkeypatch, *, agent="nda", band="low", counts=None, note=None, contract_id="c-1"):
    contract = SimpleNamespace(id="c-1", metadata_json={})
    monkeypatch.setattr(service, "_get_contract", lambda db, cid: contract)
    monkeypatch.setattr(risk, "contract_risk", lambda db, c: _risk(band, counts, note))
    step = {"type": "ai_task", "name": "AI review",
            "config": {"agent": agent, "escalate_below_confidence": 0.6}}
    sr = SimpleNamespace(status="running", note=None, result=None, updated_at=None)
    run = SimpleNamespace(id="run-1", org_id="org-1", request_id="req-1", contract_id=contract_id,
                          flow_name="NDA — their paper")
    outcome = asyncio.run(service._execute_step(FakeDB(), run=run, step=step, sr=sr,
                                                actor=SimpleNamespace(id="u-1")))
    return outcome, sr


def test_an_nda_step_reads_the_contracts_playbook_review_not_the_keywords(monkeypatch):
    outcome, sr = _run_step(monkeypatch, band="low")
    assert outcome == "advance"
    assert sr.result["source"] == "playbook_review"
    assert sr.result["risk_band"] == "low"
    assert "No analysis ran" not in (sr.note or "")


def test_an_nda_with_open_high_findings_goes_to_a_person_and_says_why(monkeypatch):
    outcome, sr = _run_step(monkeypatch, band="high", counts={"critical": 0, "high": 3, "medium": 1, "low": 0})
    assert outcome == "wait"
    assert sr.status == "waiting_human"
    assert sr.result["source"] == "playbook_review"
    assert "3 high" in sr.result["summary"]
    assert "No analysis ran" not in (sr.note or "")


def test_an_nda_never_reviewed_asks_a_person_and_says_it_was_never_reviewed(monkeypatch):
    outcome, sr = _run_step(monkeypatch, band="unknown", note="Never reviewed against the NDA playbook.")
    assert outcome == "wait"
    assert sr.status == "waiting_human"
    assert "Never reviewed" in sr.note


def test_the_msa_agent_still_takes_the_same_path(monkeypatch):
    outcome, sr = _run_step(monkeypatch, agent="contract-review-agent", band="medium")
    assert outcome == "advance"
    assert sr.result["source"] == "playbook_review"


def test_an_nda_request_with_no_contract_yet_still_falls_back_and_says_so(monkeypatch):
    outcome, sr = _run_step(monkeypatch, band="low", contract_id=None)
    assert outcome == "wait"
    assert "No analysis ran" in sr.note
    assert sr.result["source"] == "regex"


@pytest.mark.parametrize("agent", ["nda", "nda-agent", "nda_agent"])
def test_every_spelling_of_the_nda_agent_is_a_playbook_review(agent):
    assert service._AGENT_KEY_MAP[agent] in service._PLAYBOOK_REVIEW_AGENTS
