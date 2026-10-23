"""Keeping a high or critical risk is a decision that needs a reason.

Before, "waived" could be recorded for any issue with no explanation (and Reject did it
for you). For a high or critical issue the server now refuses to record that the risk
stays unless a reason is given, whichever screen sent the request. Fixing the issue,
escalating it, or waiving a low/medium one needs no reason.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.models  # noqa: F401  registers every domain's models for mapper config
from app.playbooks import routes, service
from app.playbooks.schemas import PlaybookDecisionCreate

KEEPS_THE_RISK = ["waived", "accepted", "accepted_fallback"]


@pytest.mark.parametrize("severity", ["high", "critical", "High"])
@pytest.mark.parametrize("decision", KEEPS_THE_RISK)
def test_keeping_a_high_risk_needs_a_reason(severity, decision):
    for blank in (None, "", "   ", "ok"):
        problem = service.decision_problem(severity=severity, decision=decision, rationale=blank)
        assert problem and "reason" in problem.lower()
    assert service.decision_problem(severity=severity, decision=decision, rationale="Customer's standard term, signed off by the GC") is None


@pytest.mark.parametrize("decision", ["fixed", "escalated", "rejected"])
def test_other_decisions_on_a_high_risk_need_no_reason(decision):
    assert service.decision_problem(severity="high", decision=decision, rationale=None) is None


@pytest.mark.parametrize("severity", ["low", "medium", None, "their_change"])
@pytest.mark.parametrize("decision", KEEPS_THE_RISK)
def test_lower_risks_can_still_be_kept_without_a_reason(severity, decision):
    assert service.decision_problem(severity=severity, decision=decision, rationale=None) is None


class _DB:
    def __init__(self, deviation):
        self.deviation = deviation
        self.commits = 0

    def get(self, _model, _key):
        return self.deviation

    def commit(self):
        self.commits += 1

    def refresh(self, _row):
        pass


class _Service:
    def __init__(self):
        self.calls = []

    def record_deviation_decision(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(id="dec-1")


@pytest.fixture
def route(monkeypatch):
    monkeypatch.setattr(routes, "get_contract_for_user", lambda *a, **k: object())
    user = SimpleNamespace(id="u-1", org_id="org-1")

    def call(severity, decision, rationale=None):
        deviation = SimpleNamespace(id="d-1", org_id="org-1", contract_id="c-1", severity=severity)
        svc, db = _Service(), _DB(deviation)
        try:
            routes.decide_deviation(
                "d-1", PlaybookDecisionCreate(decision=decision, rationale=rationale),
                db=db, current_user=user, service=svc)
        except HTTPException as exc:
            return exc, svc, db
        return None, svc, db

    return call


def test_the_route_refuses_to_keep_a_high_risk_with_no_reason(route):
    exc, svc, db = route("high", "waived")
    assert exc is not None and exc.status_code == 422 and "reason" in exc.detail.lower()
    assert svc.calls == [] and db.commits == 0


def test_the_route_records_it_once_a_reason_is_given(route):
    exc, svc, db = route("critical", "waived", "Accepted by the CFO on 3 Oct")
    assert exc is None and db.commits == 1
    assert svc.calls[0]["rationale"] == "Accepted by the CFO on 3 Oct"


def test_the_route_still_closes_a_fixed_high_risk_without_a_reason(route):
    exc, svc, _ = route("high", "fixed")
    assert exc is None and len(svc.calls) == 1
