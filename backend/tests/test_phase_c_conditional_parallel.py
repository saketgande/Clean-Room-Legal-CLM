"""Phase C — conditional & parallel steps in the resolver + preview.

Stage grouping and condition evaluation are pure; the submit/decide state
machine (parallel-stage advancement, conditional SKIPPED) is exercised
end-to-end against a live stack.
"""
from types import SimpleNamespace

from app.approvals import service as svc
from app.approvals.service import _condition_met, preview_routing, resolve_chain


def contract(**kw):
    base = dict(
        id="c1", value_amount=None, contract_type=None, risk_band=None, risk_level=None,
        risk_score=None, counterparty_name=None, jurisdiction=None, currency=None, title=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def step(group=None, order=1, stage=None, condition=None, mode="any"):
    return SimpleNamespace(
        step_order=order, stage=stage, condition=condition, approver_group_id=group,
        approver_user_id=None, approver_role=None, mode=mode,
    )


def rule(rid, priority, criteria, steps):
    return SimpleNamespace(
        id=rid, name=rid, priority=str(priority), criteria=criteria, steps=list(steps),
        approver_user_id=None, approver_role=None,
    )


class FakeDB:
    def __init__(self, rules):
        self._rules = rules

    def scalars(self, *a, **k):
        return SimpleNamespace(all=lambda: list(self._rules))

    def scalar(self, *a, **k):
        return 0


def test_condition_met_operators():
    c = contract(value_amount=2_000_000)
    assert _condition_met(None, c) is True
    assert _condition_met([], c) is True
    assert _condition_met([{"field": "value_amount", "op": "gt", "value": 1_000_000}], c) is True
    assert _condition_met([{"field": "value_amount", "op": "gt", "value": 3_000_000}], c) is False
    # multiple conditions are AND-ed
    both = [
        {"field": "value_amount", "op": "gte", "value": 1_000_000},
        {"field": "value_amount", "op": "lte", "value": 5_000_000},
    ]
    assert _condition_met(both, c) is True


def test_resolve_chain_groups_parallel_steps_by_stage(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", True)
    rules = [
        rule("R", 10, {}, [
            step(group="LEGAL", order=1, stage=1),
            step(group="FIN", order=2, stage=2),
            step(group="PROC", order=3, stage=2),  # parallel with FIN
        ])
    ]
    chain = resolve_chain(FakeDB(rules), contract=contract(), org_id="o")
    by_stage: dict[int, list[str]] = {}
    for t in chain:
        by_stage.setdefault(t["stage"], []).append(t["approver_group_id"])
    assert by_stage == {1: ["LEGAL"], 2: ["FIN", "PROC"]}


def test_resolve_chain_remaps_stages_across_composed_rules(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", True)
    rules = [
        rule("R1", 10, {}, [step(group="LEGAL", order=1, stage=1), step(group="FIN", order=2, stage=1)]),
        rule("R2", 20, {}, [step(group="COMP", order=1, stage=1)]),
    ]
    chain = resolve_chain(FakeDB(rules), contract=contract(), org_id="o")
    stages = {t["approver_group_id"]: t["stage"] for t in chain}
    # R1's two steps stay parallel in stage 1; R2's step sequences into stage 2.
    assert stages == {"LEGAL": 1, "FIN": 1, "COMP": 2}


def test_resolve_chain_carries_condition(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", True)
    cond = [{"field": "value_amount", "op": "gt", "value": 1_000_000}]
    rules = [rule("R", 10, {}, [step(group="EXEC", order=1, stage=1, condition=cond)])]
    chain = resolve_chain(FakeDB(rules), contract=contract(), org_id="o")
    assert chain[0]["condition"] == cond


def test_preview_marks_conditional_step_skipped(monkeypatch):
    monkeypatch.setattr(svc.settings, "routing_compose_matched_rules", True)
    monkeypatch.setattr(svc.settings, "nda_fast_lane_enabled", False)
    cond = [{"field": "value_amount", "op": "gt", "value": 1_000_000}]
    rules = [
        rule("R", 10, {}, [
            step(group="LEGAL", order=1, stage=1),
            step(group="EXEC", order=2, stage=2, condition=cond),
        ])
    ]
    # $500k is under the $1M threshold, so the Executive step is skipped.
    prev = preview_routing(FakeDB(rules), contract=contract(value_amount=500_000), org_id="o")
    skipped = {t["approver_group_id"]: t["skipped"] for t in prev["chain"]}
    assert skipped == {"LEGAL": False, "EXEC": True}
    # …and triggered when the contract clears the threshold.
    prev2 = preview_routing(FakeDB(rules), contract=contract(value_amount=2_000_000), org_id="o")
    skipped2 = {t["approver_group_id"]: t["skipped"] for t in prev2["chain"]}
    assert skipped2 == {"LEGAL": False, "EXEC": False}
