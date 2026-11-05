"""The paid obligation rehearsal (`python -m eval.exam obligations`), run on a fake model: nothing here can reach the
network or spend money. Covers what is asked of the model, what the owner is shown before spending, the ceiling, a
failed call, what is saved, and what reaches the ledger. The "live database" is the exam tests' scratch Postgres
database (tests/test_exam_gate.py), so no `eval.*` audit row lands in the shared one."""

from __future__ import annotations

import asyncio
import hashlib
import json

import anthropic
import httpx
import pytest
from sqlalchemy import select

import app.models  # noqa: F401  (register every mapper)
from app.ai.context import _risk_facts
from app.ai.gateway import AICallContext, AIGateway
from app.ai.gateway.features import feature_registry
from app.ai.registry import skill_registry
from app.contracts.models import Contract
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.models import AICallLog
from app.integrations import claude
from app.integrations.claude import ClaudeClient
from eval.exam import env
from eval.exam import runner as exam_runner
from eval.exam.env import ExamRefused
from eval.obligations import adapters, gold
from eval.obligations import runner as rehearsal
from tests.exam_fake_anthropic import install
from tests.test_ai_gateway import FakeProvider, _response
from tests.test_exam_gate import OWNER, Typist, audit_rows, make_live, scratch_database

SKILL = rehearsal.SKILL


@pytest.fixture(scope="module")
def scratch_engine():
    with scratch_database() as engine:
        yield engine


@pytest.fixture
def live(scratch_engine):
    return make_live(scratch_engine)


@pytest.fixture(autouse=True)
def _real_model_off_and_prompts_from_the_test_database(monkeypatch):
    """The command refuses a mock model, so the tests switch the mock off and hand it a fake provider instead. The
    scratch 'live' database holds four tables, not the prompt tables, so the prompt is read from the test database."""
    monkeypatch.setattr(settings, "mock_claude", False)
    monkeypatch.setattr(claude, "exam_meter", None)
    real = rehearsal.get_active_prompt_bundle

    def bundle(_db, **kwargs):
        with SessionLocal() as db:
            return real(db, **kwargs)

    monkeypatch.setattr(rehearsal, "get_active_prompt_bundle", bundle)


def _answer(key: gold.Gold) -> dict:
    """What a perfect model would send back for this key: one row per duty or restriction."""
    return {"obligations": [
        {"obligation_type": r["type"], "description": r["description"], "responsible_party": r["responsible_party"],
         "timing": r["timing"], "citations": [{"quote": r["quote"]}]}
        for r in adapters.rows_from_gold(key)]}


def _provider(key: gold.Gold | None = None, **kw) -> FakeProvider:
    tool = feature_registry.get(SKILL).tool_name
    key = key or gold.load_all()[0]
    return FakeProvider(_response(tool=tool, tool_input=_answer(key), stop="tool_use"), **kw)


def _run(live, provider, tmp_path, *, ask=None, runs=1, label="test", **kwargs):
    return asyncio.run(rehearsal.run_obligations(
        live=live, owner=OWNER, ask=ask or Typist("5"), runs=runs, label=label, provider=provider,
        results_dir=tmp_path, **kwargs))


def _ledger(live, run_id):
    with live.sessions() as db:
        return list(db.scalars(select(AICallLog).where(AICallLog.eval_run_id == run_id)))


# --- what is asked of the model ---------------------------------------------------------------------------------


def test_the_request_is_the_production_prompt_with_the_keys_text_and_metadata(live, tmp_path):
    """Guards the rehearsal measuring something other than production: the skill prompt, the job's own prompt
    builder, the key's frozen text and the metadata block the stored contract shows, at the production model and
    output limit."""
    key = gold.load_all()[0]
    provider = _provider(key)
    _run(live, provider, tmp_path)

    [(_, kwargs)] = provider.calls
    spec = skill_registry.get(SKILL)
    assert f"Skill: {spec.name} v{spec.version}" in kwargs["user_prompt"]
    assert key.text in kwargs["user_prompt"]
    shown = json.dumps(key.metadata)  # the builder writes the manifest as JSON, so a dash is escaped, as in production
    for field in ("title", "contract_type", "counterparty_name"):
        assert json.dumps(key.metadata[field])[1:-1] in kwargs["user_prompt"], shown
    assert kwargs["max_tokens"] == spec.max_tokens and kwargs["tool_name"] == feature_registry.get(SKILL).tool_name


def test_the_schema_the_model_is_given_carries_the_column_bounds(live, tmp_path):
    """Guards the hygiene fix not reaching the model: the bound only prevents the overflow if the tool schema sent
    with the request says it."""
    provider = _provider()
    _run(live, provider, tmp_path)

    schema = json.dumps(provider.calls[0][1]["input_schema"])
    assert '"maxLength": 160' in schema and '"maxLength": 255' in schema


def test_the_estimate_prices_exactly_the_request_the_client_sends(live):
    """Guards the typed ceiling being honest: the estimate and the meter's worst case are computed from the request
    this module builds, which must be the one ClaudeClient builds, or a ceiling typed from the estimate could be
    refused by the meter, or the estimate could be wrong."""
    key = gold.load_all()[0]
    spec = skill_registry.get(SKILL)
    gateway = AIGateway(provider=_provider(key))
    bundle = gateway.prompt_for(SessionLocal(), SKILL, org_id=live.org_id)
    items, plan = rehearsal._prepare([key], gateway, bundle, spec, AICallContext(org_id=live.org_id))
    sent: list[dict] = []

    class Stop(Exception):
        pass

    async def capture(self, *, org_id, json_payload):
        sent.append(json_payload)
        raise Stop

    claude_post = ClaudeClient._post_messages
    ClaudeClient._post_messages = capture
    try:
        with pytest.raises(Stop):
            asyncio.run(ClaudeClient().complete_structured(
                org_id=None, system_prompt=plan.system_prompt, user_prompt=items[0].user_prompt,
                tool_name=plan.feature.tool_name, input_schema=gateway._require_structured(plan, None),
                max_tokens=plan.max_tokens, temperature=plan.feature.temperature, model=plan.model))
    finally:
        ClaudeClient._post_messages = claude_post
    assert sent == [items[0].payload]
    estimate = rehearsal._estimate(items, plan, runs=2)
    assert estimate.calls == 2 and estimate.worst_case_usd == 2 * exam_runner.worst_cost(sent[0], batched=False)
    assert 0 < estimate.usd < estimate.worst_case_usd


# --- a run ------------------------------------------------------------------------------------------------------


def test_a_perfect_answer_scores_perfectly_and_everything_is_recorded(live, tmp_path):
    """Guards the whole path with a model that gives the key's own answer: the score must be 100 percent, the run
    must be on the audit trail as an `obligations` run, the call on the ledger as exam traffic (with no contract
    text in it), and the numbers saved and stamped as a rehearsal."""
    key = gold.load_all()[0]
    provider = _provider(key)
    typist = Typist("5")

    result = _run(live, provider, tmp_path, ask=typist)

    assert result["outcome"] == "complete" and len(provider.calls) == 1
    [call] = result["calls"]
    assert call["ok"] and call["score"]["recall"] == 1 and call["score"]["precision"] == 1
    assert not call["score"]["invented_dates"]
    [row] = _ledger(live, result["run_id"])
    assert row.status == "succeeded" and row.prompt_key == SKILL and row.eval_run_id == result["run_id"]
    assert row.input_payload == {"key": key.id, "run": 0, "chars": len(key.text)}
    assert key.text[:60] not in json.dumps(row.input_payload)
    [approved] = audit_rows(live, "eval.spend_approved")
    assert approved.after["kind"] == "obligations" and approved.after["ceiling_usd"] == 5.0
    [finished] = audit_rows(live, "eval.run_finished")
    saved = tmp_path / result["run_id"]
    assert finished.after["outcome"] == "complete" and finished.after["headline"]["provisional"] is True
    assert finished.after["results_sha256"] == hashlib.sha256((saved / "results.json").read_bytes()).hexdigest()
    card = (saved / "scorecard.txt").read_text(encoding="utf-8")
    assert card.startswith(rehearsal.BANNER) and "KEY STATUS: draft" in card and "Limits:" in card
    assert claude.exam_meter is None, "the meter must not outlive the run it was approved for"


def test_the_meter_is_installed_while_the_call_is_made(live, tmp_path):
    """Guards the ceiling being decoration: the exam's meter has to be in place when the client is called, because
    that is where it refuses a call that could pass the ceiling."""
    seen = []

    class Watching(FakeProvider):
        async def complete_structured(self, **kwargs):
            seen.append(claude.exam_meter)
            return await super().complete_structured(**kwargs)

    key = gold.load_all()[0]
    provider = Watching(_provider(key).response)
    _run(live, provider, tmp_path)

    assert len(seen) == 1 and isinstance(seen[0], exam_runner.Meter) and seen[0].ceiling_usd == 5.0


def test_a_failed_call_is_a_result_with_its_outcome_not_a_crash(live, tmp_path):
    """Guards a provider that was busy aborting the run with a traceback, or being scored as an empty extraction
    (recall 0): a call with no answer has an outcome, in plain words, and no score."""
    busy = anthropic.APIStatusError(
        "overloaded", response=httpx.Response(529, request=httpx.Request("POST", "https://api.anthropic.com")),
        body=None)
    result = _run(live, _provider(error=busy), tmp_path)

    [call] = result["calls"]
    assert not call["ok"] and call["failure"]["outcome"] == "transient" and "score" not in call
    assert result["outcome"] == "complete"
    [finished] = audit_rows(live, "eval.run_finished")
    assert finished.after["contains_errors"] is True and finished.after["headline"]["outcomes"] == {"transient": 1}
    card = (tmp_path / result["run_id"] / "scorecard.txt").read_text(encoding="utf-8")
    assert "NO ANSWER - transient" in card and "recall" not in card


def test_the_ceiling_stops_the_run_and_the_refused_call_is_not_a_failed_call(live, tmp_path):
    """Guards the hard limit: when the meter refuses a call the run stops, is recorded as incomplete (so it can
    never be part of a baseline), and the refused call, which never left the machine, is not counted as a failure."""
    refused = exam_runner.CeilingReached("spending ceiling $0.01 reached")
    provider = _provider(error=refused)

    result = _run(live, provider, tmp_path)

    assert result["outcome"] == "incomplete" and result["calls"] == []
    [finished] = audit_rows(live, "eval.run_finished")
    assert finished.after["incomplete_reason"] == "ceiling_reached" and finished.after["failed_call_rate"] == 0
    assert _ledger(live, result["run_id"]) == []


@pytest.fixture
def real_client(monkeypatch):
    """The REAL request path (ClaudeClient, the exam meter, retries) on the exam tests' fake Anthropic: no key, no
    network, no cent. The daily-cap calls are stubbed (they would need Redis)."""
    fake = install(monkeypatch)
    monkeypatch.setattr(claude, "reserve_tokens", lambda *_a: None)
    monkeypatch.setattr(claude, "settle_tokens", lambda *_a: None)
    return fake


def test_a_ceiling_too_low_for_the_worst_case_is_refused_by_the_real_meter_before_anything_is_sent(
        live, tmp_path, real_client):
    """Guards the hard limit end to end, through the real client and the real meter: a call that could cost more than
    the typed ceiling never reaches the provider, and the run is recorded as incomplete. (The fake provider in the
    other tests does not consult the meter; this one does.)"""
    result = _run(live, None, tmp_path, ask=Typist("0.01"))

    assert real_client.streamed == [] and result["calls"] == []
    assert result["outcome"] == "incomplete"
    [finished] = audit_rows(live, "eval.run_finished")
    assert finished.after["incomplete_reason"] == "ceiling_reached" and finished.after["actual"]["usd"] == 0
    assert _ledger(live, result["run_id"]) == []


def test_the_real_client_sends_exactly_the_request_the_estimate_priced_and_stays_under_the_ceiling(
        live, tmp_path, real_client):
    """Guards the typed ceiling being honest: with the suggested ceiling the call goes through the real meter, what is
    sent is the request the estimate was computed from, and the ledger's cost is within the ceiling."""
    key = gold.load_all()[0]
    result = _run(live, None, tmp_path, ask=Typist("1"))

    [sent] = real_client.streamed
    gateway = AIGateway(provider=_provider(key))
    bundle = gateway.prompt_for(SessionLocal(), SKILL, org_id=live.org_id)
    [item], _ = rehearsal._prepare([key], gateway, bundle, skill_registry.get(SKILL), AICallContext(org_id=live.org_id))
    assert sent == item.payload
    [call] = result["calls"]
    assert result["outcome"] == "complete" and call["ok"]
    [finished] = audit_rows(live, "eval.run_finished")
    assert 0 < finished.after["actual"]["usd"] <= 1.0


def test_the_ledger_keeps_what_a_call_cost_not_what_it_said(live, tmp_path, monkeypatch):
    """Guards the exam's rule that no contract text lands on the live ledger: the gateway stores the validated answer
    (every quote copied from the key) and, with AI_STORE_RAW_OUTPUTS on, the raw reply. The run's rows are scrubbed;
    the answer lives in results.json."""
    monkeypatch.setattr(settings, "ai_store_raw_outputs", True)
    key = gold.load_all()[0]
    result = _run(live, _provider(key), tmp_path)

    [row] = _ledger(live, result["run_id"])
    assert row.status == "succeeded" and row.prompt_tokens and row.completion_tokens  # what it cost is kept
    assert row.validated_output is None and row.raw_ai_output is None
    saved = json.loads((tmp_path / result["run_id"] / "results.json").read_text(encoding="utf-8"))
    assert saved["calls"][0]["rows"], "the answer itself is kept in the run's own results file"


def test_a_call_the_ledger_has_no_row_for_cannot_make_a_paid_run_look_free_or_clean(live, tmp_path, monkeypatch):
    """Guards the immutable run record being the ledger's word alone: if a call's row was lost (a failed write after
    the money was spent) the record would say 0 calls, $0 and no errors. The meter's own figure is kept beside it
    and fewer rows than calls counts as an error."""
    real = exam_runner.ledger_actual
    monkeypatch.setattr(exam_runner, "ledger_actual", lambda *a, **k: {**real(*a, **k), "calls": 0, "failed_calls": 0})

    _run(live, _provider(), tmp_path)

    [finished] = audit_rows(live, "eval.run_finished")
    assert finished.after["contains_errors"] is True
    assert finished.after["headline"]["ledger_rows_missing"] == 1 and finished.after["headline"]["metered_usd"] is not None


def test_a_key_id_must_fit_the_ledger_column(tmp_path):
    """Guards the id being written to a_i_call_log.resource_id (36 characters) with no check: a longer id lost the
    call's row after the money was spent. The rule is tied to the real column, so shrinking it fails here."""
    assert AICallLog.__table__.c.resource_id.type.length == 36
    source = json.loads((gold.ROOT / "gold" / "msa-small.json").read_text(encoding="utf-8"))
    for ident, fine in (("x" * 36, True), ("x" * 37, False), ("Has Spaces", False), ("", False)):
        path = tmp_path / "key.json"
        path.write_text(json.dumps({**source, "id": ident}), encoding="utf-8")
        refused = any("id must be" in issue for issue in gold.problems(path))
        assert refused is not fine, ident


def test_the_limits_name_the_risk_lines_the_rehearsal_prompt_leaves_out():
    """Guards the stamped limits claiming a like-for-like prompt: production's manifest carries three risk lines for
    this skill that a text-only context does not, so the limits must say so."""
    assert set(_risk_facts(Contract())) == {"risk_band", "risk_counts", "risk_note"}
    assert all(name in rehearsal.LIMITS for name in _risk_facts(Contract()))


@pytest.mark.parametrize("ceiling", ["0", "-1", "nan", "inf", "abc", ""])
def test_the_ceiling_flag_must_be_a_positive_number_and_is_checked_before_anything_runs(ceiling):
    """Guards a typo on the command line (zero, negative, not a number) reaching the spending gate: it is refused by
    the parser, before the process is isolated or anything is shown."""
    from eval.exam import __main__ as exam_cli

    with pytest.raises(SystemExit) as refused:
        exam_cli.main(["obligations", "--ceiling", ceiling])
    assert refused.value.code == 2


def test_the_ceiling_flag_is_the_go_ahead_given_after_the_estimate_and_is_recorded_as_such(
        live, tmp_path, monkeypatch, capsys):
    """Guards the flag skipping the owner's look at the price: the estimate is printed first, then the ceiling from the
    command line is shown as the go-ahead, and the immutable approval row says it was given on the command line."""
    from eval.exam import __main__ as exam_cli

    provider = _provider()
    real = rehearsal.run_obligations

    async def with_fake_model(**kwargs):
        return await real(**kwargs, provider=provider, results_dir=tmp_path)

    monkeypatch.setattr(rehearsal, "run_obligations", with_fake_model)
    exam_cli._obligations(exam_cli._parser().parse_args(["obligations", "--ceiling", "1"]), live, OWNER)

    out = capsys.readouterr().out
    assert out.index("Estimate for this obligations run") < out.index("(from --ceiling)")
    assert "in USD (press Enter to cancel): 1  (from --ceiling)" in out
    [approved] = audit_rows(live, "eval.spend_approved")
    assert approved.after["ceiling_usd"] == 1.0 and approved.after["ceiling_source"] == "command_line"
    assert len(provider.calls) == 1


def test_without_the_flag_the_prompt_is_used_and_recorded_as_such(live, tmp_path):
    _run(live, _provider(), tmp_path)
    [approved] = audit_rows(live, "eval.spend_approved")
    assert approved.after["ceiling_source"] == "prompt"


def test_the_flag_does_not_remove_the_terminal_requirement(live, monkeypatch):
    """Guards the flag becoming a way for a script or an agent to approve spending: the command still refuses unless a
    person is at a terminal, flag or no flag."""
    import sys

    from eval.exam import __main__ as exam_cli

    class NoTerminal:
        def isatty(self):
            return False

    monkeypatch.setattr(sys, "stdin", NoTerminal())
    args = exam_cli._parser().parse_args(["obligations", "--ceiling", "1"])
    with pytest.raises(ExamRefused, match="needs a terminal"):
        exam_cli._authenticate(live, args)


def test_a_second_exam_command_cannot_run_alongside_it(live):
    """Guards a concurrent `exam run` closing this run as 'crashed' (it treats every approved but unfinished run as
    dead) and a second run record following: the command takes the exam lock, as `run` does."""
    from eval.exam import __main__ as exam_cli

    args = exam_cli._parser().parse_args(["obligations"])
    with env.exam_lock(live), pytest.raises(ExamRefused, match="another exam command is running"):
        exam_cli._obligations(args, live, OWNER)


def test_without_a_typed_ceiling_nothing_is_sent(live, tmp_path):
    """Guards the go-ahead: pressing Enter at the ceiling prompt cancels with nothing sent and nothing saved."""
    provider = _provider()
    with pytest.raises(ExamRefused, match="nothing was sent"):
        _run(live, provider, tmp_path, ask=Typist(approve=False))
    assert provider.calls == [] and list(tmp_path.iterdir()) == []
    assert audit_rows(live, "eval.spend_approved") == [] and len(audit_rows(live, "eval.spend_refused")) == 1


def test_two_runs_report_how_stable_the_answers_were(live, tmp_path):
    """Guards the number the later noise band is built from: with two runs the scorecard says how many of the
    duties found in any run were found in every run."""
    provider = _provider()
    result = _run(live, provider, tmp_path, runs=2)

    assert len(provider.calls) == 2 and [c["run"] for c in result["calls"]] == [0, 1]
    assert "stability msa-small: 100%" in (tmp_path / result["run_id"] / "scorecard.txt").read_text(encoding="utf-8")


def test_stability_counts_duties_found_every_time_over_duties_found_any_time():
    key = gold.load_all()[0]
    headline = [a.id for a in key.atoms if a.kind in gold.HEADLINE_KINDS]
    keys = {key.id: key}

    def call(missed):
        return {"ok": True, "key": key.id, "score": {"missed": [{"id": i} for i in missed]}}

    assert rehearsal.stability(keys, [call([]), call([])]) == {key.id: 1.0}
    one_apart = rehearsal.stability(keys, [call([headline[0]]), call([headline[1]])])
    assert one_apart[key.id] == pytest.approx((len(headline) - 2) / len(headline))
    assert rehearsal.stability(keys, [call([])]) == {}  # one run says nothing about stability
    assert rehearsal.stability(keys, [{"ok": False, "key": key.id}, call([])]) == {}


# --- the guards --------------------------------------------------------------------------------------------------


def test_a_fake_model_is_refused(live, tmp_path, monkeypatch):
    """Guards scoring a mock's canned answers as if the real model gave them."""
    monkeypatch.setattr(settings, "mock_claude", True)
    with pytest.raises(ExamRefused, match="MOCK_CLAUDE"):
        _run(live, _provider(), tmp_path)


@pytest.mark.parametrize("runs", [0, 4])
def test_the_number_of_runs_is_bounded(live, tmp_path, runs):
    with pytest.raises(ExamRefused, match="--runs"):
        _run(live, _provider(), tmp_path, runs=runs)


def test_no_answer_keys_is_refused(live, tmp_path, monkeypatch):
    provider = _provider()  # built while the key still loads
    monkeypatch.setattr(gold, "key_files", list)
    with pytest.raises(ExamRefused, match="no answer keys"):
        _run(live, provider, tmp_path)
    assert provider.calls == []


def test_the_command_is_wired_into_the_exam_as_a_gated_paid_command():
    """Guards the command running without the exam's guards: it must be owner-only and paid (so `isolate` demands
    the exam's own key and moves the process onto the throwaway database), and its run kind must be a known one."""
    from eval.exam import __main__ as exam_cli

    assert exam_cli._COMMANDS["obligations"] == (True, True)
    assert "obligations" in exam_runner._RUN_KINDS
    args = exam_cli._parser().parse_args(["obligations", "--runs", "2", "--label", "after-bounds"])
    assert (args.command, args.runs, args.label) == ("obligations", 2, "after-bounds")
    assert exam_cli._HANDLERS["obligations"] is exam_cli._obligations
