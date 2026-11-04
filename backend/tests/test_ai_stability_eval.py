"""Guards for eval/ai_stability.py: the decision comparators, the metric maths and the free plan path.
No AI is called anywhere in this file."""

from __future__ import annotations

from eval import ai_stability as s


def test_normalisers_ignore_wording_not_values():
    """Failure mode: 'Acme, Inc.' vs 'ACME Inc' or 1000000 vs '1,000,000' read as a changed decision,
    inflating the variance ranking with formatting noise."""
    assert s.norm_text("Acme,  Inc.") == s.norm_text("ACME inc")
    assert s.norm_date("2026-12-30T00:00:00") == "2026-12-30"
    assert s.norm_num("1,000,000") == s.norm_num(1000000.0)
    assert s.norm_type("Confidentiality obligations") == s.norm_type("confidentiality")
    assert s.norm_party("The Receiving Party") == s.norm_party("receiving party")
    assert s.norm_text("Acme") != s.norm_text("Initech")


def test_playbook_review_ignores_preferred_and_wording():
    """Failure mode: counting items the app discards (position preferred) or reworded issues as instability."""
    a = {"deviations": [{"rule_index": 1, "clause_type": "x", "position": "outside", "issue": "one way"},
                        {"rule_index": 2, "clause_type": "y", "position": "preferred", "issue": "fine"}]}
    b = {"deviations": [{"rule_index": 1, "clause_type": "x", "position": "outside", "issue": "other words"}]}
    c = {"deviations": [{"rule_index": 1, "clause_type": "x", "position": "fallback", "issue": "one way"}]}
    assert s.cmp_playbook_review(a) == s.cmp_playbook_review(b)
    assert s.cmp_playbook_review(a) != s.cmp_playbook_review(c)


def test_each_comparator_sees_a_real_decision_change():
    """Failure mode: a comparator reading a field name that does not exist (renamed schema) returns the same
    empty set for every run and reports 100% stable forever."""
    cases = {
        "intake_triage": ({"category": "NDA", "urgency": "High"}, {"category": "Vendor", "urgency": "High"}),
        "intake_form_read": ({"mismatches": [{"field": "value", "same": False, "text_says": "5"}]},
                             {"mismatches": []}),
        "email_triage_agent": ({"is_clm_related": True, "category": "NDA"}, {"is_clm_related": False}),
        "contract_metadata_extraction": ({"effective_date": "2026-01-01"}, {"effective_date": "2026-02-01"}),
        "clause_labeling": ({"labels": [{"segment_id": "S1", "clause_type": "a"}]},
                            {"labels": [{"segment_id": "S1", "clause_type": "b"}]}),
        "renewal_extraction": ({"auto_renews": True}, {"auto_renews": False}),
        "obligation_extraction": ({"obligations": [{"obligation_type": "Payment", "timing": {"kind": "ongoing"}}]},
                                  {"obligations": []}),
        "contract_brain_answer": ({"related_contract_ids": ["c1"]}, {"related_contract_ids": ["c2"]}),
        "portfolio_filter": ({"stages": ["active"]}, {"stages": ["closed"]}),
        "tabular_cell_extraction": ({"verdict": "yes"}, {"verdict": "no"}),
        "contract_edit_suggestions": ({"edits": [{"original_text": "A clause."}]},
                                      {"edits": [{"original_text": "Another clause."}]}),
        "playbook_review": ({"deviations": [{"rule_index": 1, "clause_type": "x"}]}, {"deviations": []}),
    }
    assert set(cases) == set(s.COMPARATORS)
    for task, (a, b) in cases.items():
        assert s.COMPARATORS[task](a) != s.COMPARATORS[task](b), task
        assert s.COMPARATORS[task](a) == s.COMPARATORS[task](dict(a)), task


def test_obligation_order_and_wording_of_type_do_not_matter():
    """Failure mode: the model listing the same duties in another order flagged as different."""
    ob = lambda t, p: {"obligation_type": t, "responsible_party": p, "timing": {"kind": "ongoing"}}
    a = {"obligations": [ob("Payment", "Customer"), ob("Confidentiality obligations", "The Receiving Party")]}
    b = {"obligations": [ob("confidentiality", "Receiving Party"), ob("payment", "customer")]}
    assert s.cmp_obligations(a) == s.cmp_obligations(b)


def test_jaccard_and_input_metrics():
    """Failure mode: wrong set maths (empty vs empty must be identical, partial overlap must be fractional)."""
    assert s.jaccard(set(), set()) == 1.0
    assert s.jaccard({1, 2}, {2, 3}) == 1 / 3
    same = s.input_metrics([{1}, {1}, {1}], {1})
    assert same["stable"] and same["jaccard"] == 1.0 and same["matches_original"] == 3
    diff = s.input_metrics([{1, 2}, {1, 2}, {1, 3}], {1, 2})
    assert not diff["stable"] and diff["matches_original"] == 2
    assert abs(diff["jaccard"] - (1 + 1 / 3 + 1 / 3) / 3) < 1e-9
    assert s.input_metrics([{1}], None)["scored"] is False  # one surviving run cannot show stability


def test_segment_agreement_counts_an_unlabelled_segment_as_disagreement():
    """Failure mode: a run that skips a segment looking 'in agreement' with the runs that labelled it."""
    runs = [{("S1", "a"), ("S2", "b")}, {("S1", "a"), ("S2", "b")}, {("S1", "a")}]
    assert s.segment_agreement(runs) == 0.5


def test_ranking_puts_most_variable_first_and_unscored_last():
    """Failure mode: ranking by task name or putting an unmeasured task at the top."""
    tasks = {
        "steady": s.task_metrics([s.input_metrics([{1}, {1}], {1})]),
        "wobbly": s.task_metrics([s.input_metrics([{1}, {2}], {1}), s.input_metrics([{1}, {1}], {1})]),
        "none": s.task_metrics([s.input_metrics([{1}], None)]),
    }
    assert s.rank(tasks) == ["wobbly", "steady", "none"]


def test_stored_markers_and_payload_hash():
    """Failure mode: replaying a ledger input that was redacted or cut, which tests a different question."""
    assert s._has_marker({"q": "abc<truncated length=900>"})
    assert s._has_marker({"q": "write to <redacted-email>"})
    assert not s._has_marker({"q": "plain"})
    assert s.payload_hash({"a": 1, "b": 2}) == s.payload_hash({"b": 2, "a": 1})


def test_refusals_cover_mock_key_and_environment():
    """Failure mode: a paid run starting under mock (meaningless) or in production (real cost, real data)."""
    class S:
        mock_claude, claude_api_key, environment = True, "", "production"
    assert len(s.refusals(S)) == 3
    S.mock_claude, S.claude_api_key, S.environment = False, "k", "local"
    assert s.refusals(S) == []


def test_plan_path_makes_no_ai_call(monkeypatch, capsys):
    """Failure mode: the dry run spending money. Any provider call raises; the plan must still print and exit 0."""
    from app.integrations import claude as claude_mod

    def boom(*a, **k):
        raise AssertionError("an AI call was made during the plan")

    for name in ("complete_structured", "complete_text", "stream_with_tools", "complete_vision_structured"):
        monkeypatch.setattr(claude_mod.ClaudeClient, name, boom, raising=False)
    assert s.main(["--tasks", "email_triage_agent,portfolio_filter"]) == 0
    out = capsys.readouterr().out
    assert "Nothing was run" in out and "TOTAL" in out


def test_old_shape_renewal_output_does_not_raise():
    """Failure mode: a stored answer from an older renewal prompt (terms as strings) raised AttributeError and
    killed a run after 84 paid calls. Old shapes must read, and a non-object must be 'uncomparable'."""
    old = {"initial_term": "12 months", "renewal_term": None, "notice_terms": ["60 days"], "auto_renews": True}
    assert s.cmp_renewal(old)
    d, err = s.safe_decision(s.cmp_renewal, "not an object")
    assert d is None and err.startswith("TypeError")
    d, err = s.safe_decision(lambda o: o["missing"], {})
    assert d is None and err.startswith("KeyError")


def test_score_input_counts_uncomparable_and_truncated_without_raising():
    """Failure mode: one odd replay aborting scoring, or a truncated replay silently shrinking the run count."""
    good = {"auto_renews": True}
    m, entry = s.score_input("renewal_extraction", "src1", "oops-a-string", "v1", [],
                             [good, good, 5], ["AIOutputInvalid: x: answer cut off at max_tokens (4096)"], 4)
    assert m["uncomparable"] == 1 and m["failed"] == 1 and m["truncated"] == 1 and m["stable"]
    assert entry["stored_uncomparable"].startswith("TypeError") and entry["uncomparable"][0].startswith("TypeError")
    assert s.task_metrics([m])["replays_truncated"] == 1


class _Row:
    def __init__(self, **kw):
        self.status, self.validated_output, self.raw_ai_output = "succeeded", None, None
        self.validation_error = self.error_class = self.stop_reason = None
        self.__dict__.update(kw)


def test_replay_outcome_reads_validated_then_raw_then_failure_reason():
    """Failure mode: not_validated standalone-agent rows (no validated_output) or truncated rows being dropped
    or counted as answers when scoring from the ledger."""
    assert s.replay_outcome(_Row(validated_output={"a": 1})) == ({"a": 1}, None)
    raw = {"content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "name": "t", "input": {"b": 2}}]}
    assert s.replay_outcome(_Row(raw_ai_output=raw)) == ({"b": 2}, None)
    out, why = s.replay_outcome(_Row(status="validation_failed", stop_reason="max_tokens",
                                     validation_error="truncated: answer cut off at max_tokens (4096)"))
    assert out is None and s.is_truncated(why)
    out, why = s.replay_outcome(_Row(status="failed", stop_reason="max_tokens", error_class="X"))
    assert s.is_truncated(why)
    assert s.replay_outcome(_Row())[0] is None


def test_from_ledger_maps_replays_to_inputs_and_writes_report(db_session, monkeypatch, tmp_path, capsys):
    """Failure mode: replay rows attached to the wrong input (or wrong task), or the free scoring path calling
    the provider / needing --yes. Fabricated rows: one source answer, three replays (one truncated)."""
    from sqlalchemy import select

    from app.auth.models import User
    from app.core.models import AICallLog

    user = db_session.scalar(select(User).limit(1))
    mk = lambda **kw: AICallLog(org_id=user.org_id, model="m", provider="claude", **kw)
    src = mk(prompt_key="email_triage_agent", status="succeeded", prompt_version="v1", input_payload={"x": 1},
             validated_output={"is_clm_related": True, "category": "NDA"})
    db_session.add(src)
    db_session.flush()
    batch = "batch-test-1"
    tag = lambda n: {"ai_stability": {"source_call_id": src.id, "run": n}}
    ok = {"is_clm_related": True, "category": "NDA"}
    for n, kw in enumerate([dict(status="succeeded", validated_output=ok),
                            dict(status="succeeded", validated_output={"is_clm_related": False}),
                            dict(status="validation_failed", stop_reason="max_tokens",
                                 validation_error="truncated: answer cut off at max_tokens (4096)")], 1):
        db_session.add(mk(prompt_key="email_triage_agent", resource_type=s.RESOURCE_TYPE, resource_id=batch,
                          input_payload=tag(n), **kw))
    db_session.flush()
    report = s.score_from_ledger(db_session, user.org_id, [batch])
    m = report["tasks"]["email_triage_agent"]["metrics"]
    assert (m["replays_planned"], m["replays_failed"], m["replays_truncated"]) == (3, 1, 1)
    assert m["pct_stable"] == 0 and m["pct_replays_match_original"] == 50
    entry = report["tasks"]["email_triage_agent"]["inputs"][0]
    assert entry["source_call_id"] == src.id and entry["disagreeing_atoms"]
    monkeypatch.setattr(s, "RESULTS", tmp_path)
    s.write_report(tmp_path / "r.json", report, complete=True)
    assert '"complete": true' in (tmp_path / "r.json").read_text()


def test_report_is_written_after_each_task(monkeypatch, tmp_path):
    """Failure mode: the report written only at the end, so a crash in a later task loses every paid result."""
    import asyncio
    import types
    from collections import Counter

    monkeypatch.setattr(s, "RESULTS", tmp_path)
    seen = []
    item = lambda t: s.Item(task=t, call_id="c" * 8, payload_hash="h", stored_output=None, stored_version=None,
                            model=None, tokens=(0, 0))

    async def fake_replay(db, user, it, batch, n):
        if it.task == "portfolio_filter":
            seen.append(list(tmp_path.glob("*.json")))  # task 1 must already be on disk
            raise RuntimeError("boom")
        return {"is_clm_related": True}

    monkeypatch.setattr(s, "replay_once", fake_replay)
    db = types.SimpleNamespace(rollback=lambda: None)
    found = {"email_triage_agent": ([item("email_triage_agent")], Counter()),
             "portfolio_filter": ([item("portfolio_filter")], Counter())}
    args = types.SimpleNamespace(as_user="x", runs=2)
    assert asyncio.run(s.run_all(args, found, db, None)) == 1
    assert seen and seen[0], "first task's results were not on disk before the second ran"
    import json
    rep = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert rep["complete"] and rep["tasks"]["portfolio_filter"]["metrics"]["replays_failed"] == 2
