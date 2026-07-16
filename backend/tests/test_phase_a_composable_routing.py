"""Phase A — composable approval routing + WHEN→THEN conditions + dry-run.

These exercise the resolver logic with fake rule/contract objects and a fake
DB (matching the suite's no-DB-pollution style): the interesting behavior is
the pure matching + composition, not SQLAlchemy.
"""
from types import SimpleNamespace

import pytest

from app.approvals import service as svc
from app.approvals.service import (
    _eval_condition,
    _matches,
    _target_key,
    preview_criteria,
    preview_routing,
    resolve_chain,
)


# ---- fakes ---------------------------------------------------------------
def contract(**kw):
    base = dict(
        id="c1", value_amount=None, contract_type=None, risk_band=None, risk_level=None,
        risk_score=None, counterparty_name=None, jurisdiction=None, currency=None, title=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def step(group=None, user=None, role=None, mode="any", order=1, stage=None, condition=None):
    return SimpleNamespace(
        step_order=order, stage=stage, condition=condition, approver_group_id=group,
        approver_user_id=user, approver_role=role, mode=mode,
    )


def rule(rid, priority, criteria, steps=(), user=None, role=None):
    return SimpleNamespace(
        id=rid, name=rid, priority=str(priority), criteria=criteria, steps=list(steps),
        approver_user_id=user, approver_role=role,
    )


class FakeDB:
    """Returns a fixed rule set for .scalars().all() and 0 for .scalar() (the
    fast-lane open-high-severity-deviation count)."""

    def __init__(self, rules):
        self._rules = rules

    def scalars(self, *a, **k):
        return SimpleNamespace(all=lambda: list(self._rules))

    def scalar(self, *a, **k):
        return 0


def groups(chain):
    return [t.get("approver_group_id") for t in chain]


# ---- _eval_condition operators ------------------------------------------
def test_eval_condition_operators():
    c = contract(value_amount=500000, contract_type="DPA", risk_band="high", risk_score=80)
    assert _eval_condition({"field": "value_amount", "op": "gte", "value": 250000}, c) is True
    assert _eval_condition({"field": "value_amount", "op": "gte", "value": 600000}, c) is False
    assert _eval_condition({"field": "value_amount", "op": "lt", "value": 600000}, c) is True
    assert _eval_condition({"field": "contract_type", "op": "eq", "value": "dpa"}, c) is True  # case-insensitive
    assert _eval_condition({"field": "contract_type", "op": "ne", "value": "nda"}, c) is True
    assert _eval_condition({"field": "contract_type", "op": "in", "value": ["nda", "dpa"]}, c) is True
    assert _eval_condition({"field": "title", "op": "exists", "value": None}, c) is False
    assert _eval_condition({"field": "risk_score", "op": "gte", "value": 70}, c) is True


def test_eval_condition_is_fail_closed():
    c = contract(value_amount=100)
    # Unknown / non-allowlisted field never matches — a typo can't widen a rule.
    assert _eval_condition({"field": "secret_backdoor", "op": "eq", "value": "x"}, c) is False
    # Non-numeric value against a numeric op fails closed rather than raising.
    assert _eval_condition({"field": "value_amount", "op": "gte", "value": "lots"}, c) is False
    # Missing attribute (None) fails everything except `exists`.
    assert _eval_condition({"field": "jurisdiction", "op": "eq", "value": "DE"}, contract()) is False


def test_risk_band_falls_back_to_risk_level():
    c = contract(risk_band=None, risk_level="critical")
    assert _eval_condition({"field": "risk_band", "op": "eq", "value": "critical"}, c) is True


# ---- _matches (legacy keys + conditions) --------------------------------
def test_matches_empty_criteria_is_catch_all():
    assert _matches(rule("r", 100, {}), contract()) is True


def test_matches_legacy_and_conditions():
    c = contract(value_amount=500000, contract_type="dpa", risk_band="high")
    assert _matches(rule("r", 10, {"min_value": 250000}), c) is True
    assert _matches(rule("r", 10, {"min_value": 600000}), c) is False
    assert _matches(rule("r", 10, {"contract_type": ["nda", "dpa"]}), c) is True
    assert _matches(rule("r", 10, {"risk_band": ["high", "critical"]}), c) is True
    # conditions AND-ed together
    crit = {"conditions": [
        {"field": "value_amount", "op": "gte", "value": 250000},
        {"field": "contract_type", "op": "eq", "value": "dpa"},
    ]}
    assert _matches(rule("r", 10, crit), c) is True
    crit_fail = {"conditions": [{"field": "value_amount", "op": "gte", "value": 900000}]}
    assert _matches(rule("r", 10, crit_fail), c) is False


def test_preview_criteria_matches_sample_without_db():
    assert preview_criteria(
        criteria={"conditions": [{"field": "value_amount", "op": "gte", "value": 250000}]},
        sample={"value_amount": 500000},
    ) is True
    assert preview_criteria(
        criteria={"conditions": [{"field": "value_amount", "op": "gte", "value": 600000}]},
        sample={"value_amount": 500000},
    ) is False


def test_target_key_dedup_identity():
    assert _target_key({"approver_group_id": "g1"}) == "g:g1"
    assert _target_key({"approver_user_id": "u1"}) == "u:u1"
    assert _target_key({"approver_role": "GC"}) == "r:gc"
    assert _target_key({}) is None


# ---- resolve_chain composition ------------------------------------------
def _demo_rules():
    return [
        rule("R3", 10, {"risk_band": ["high", "critical"]}, [step(group="LEGAL")]),
        rule("R2", 20, {"contract_type": "dpa"}, [step(group="COMP")]),
        rule("R1", 30, {"min_value": 250000}, [step(group="FIN", order=1), step(group="EXEC", order=2)]),
        rule("R4", 100, {}, [step(group="LEGAL")]),
    ]


def test_resolve_chain_composes_all_matched_rules(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", True)
    db = FakeDB(_demo_rules())
    c = contract(value_amount=500000, contract_type="dpa", risk_band="high")
    chain = resolve_chain(db, contract=c, org_id="org")
    # every matched rule contributes; LEGAL de-duplicated (R3 wins over R4).
    assert groups(chain) == ["LEGAL", "COMP", "FIN", "EXEC"]
    assert [t["step_order"] for t in chain] == [1, 2, 3, 4]
    assert {t["routing_rule_id"] for t in chain} == {"R3", "R2", "R1"}


def test_resolve_chain_legacy_single_best_when_flag_off(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", False)
    db = FakeDB(_demo_rules())
    c = contract(value_amount=500000, contract_type="dpa", risk_band="high")
    chain = resolve_chain(db, contract=c, org_id="org")
    assert groups(chain) == ["LEGAL"]  # R3 alone (lowest priority number)


def test_resolve_chain_dedup_upgrades_mode_to_all(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", True)
    rules = [
        rule("A", 10, {}, [step(group="LEGAL", mode="any")]),
        rule("B", 20, {}, [step(group="LEGAL", mode="all")]),
    ]
    chain = resolve_chain(FakeDB(rules), contract=contract(), org_id="org")
    assert groups(chain) == ["LEGAL"]
    assert chain[0]["mode"] == "all"  # stricter contributor wins


def test_resolve_chain_empty_when_no_rules(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", True)
    assert resolve_chain(FakeDB([]), contract=contract(), org_id="org") == []


# ---- preview_routing dry-run --------------------------------------------
def test_preview_routing_reports_used_and_shadowed_rules(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", True)
    db = FakeDB(_demo_rules())
    c = contract(value_amount=500000, contract_type="dpa", risk_band="high")
    prev = preview_routing(db, contract=c, org_id="org")
    assert prev["fast_lane_reason"] is None
    assert len(prev["chain"]) == 4
    used = {m["id"]: m["used"] for m in prev["matched_rules"]}
    assert used["R3"] and used["R2"] and used["R1"]
    assert used["R4"] is False  # LEGAL already present → shadowed


def test_preview_routing_fast_lane_short_circuits(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", True)
    monkeypatch.setattr(svc.settings, "nda_fast_lane_enabled", True)
    monkeypatch.setattr(svc.settings, "nda_fast_lane_max_value", 50000.0)
    db = FakeDB(_demo_rules())
    nda = contract(value_amount=30000, contract_type="nda", risk_band="low")
    prev = preview_routing(db, contract=nda, org_id="org")
    assert prev["fast_lane_reason"] is not None
    assert prev["chain"] == []
