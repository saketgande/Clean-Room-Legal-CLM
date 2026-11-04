"""The paid rehearsal of the obligation extraction: run today's pipeline once per answer key, score it, keep the numbers.

    docker compose exec backend python -m eval.exam obligations [--runs 1] [--label NAME]

It is an exam command, so every guard the exam has applies: the exam's own Anthropic key, the throwaway database for
everything the process imports, the configured owner, a terminal, and a typed spending ceiling that is a hard limit
(the exam's Meter refuses a call that could pass it). Nothing here runs in the app, in CI or on a schedule.

What is sent is the production prompt: the shipped skill prompt, the same prompt builder, the same tool schema and
model, with the key's frozen text, through the AI gateway. It does not use the job path (that would reuse a stored
answer) and saves nothing as obligations. Each call is on the AI ledger, marked as exam traffic.

The numbers are always stamped as a provisional rehearsal: the answer keys are AI-drafted drafts until a lawyer
confirms them, and a baseline is named only from confirmed keys. Import this module only after
`eval.exam.env.isolate()`: it reaches the app's models.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, update

from app.ai.context import text_only_context
from app.ai.gateway import AICallContext, AIGateway
from app.ai.outcomes import failure_from_exception
from app.ai.prompt_builder import prompt_builder
from app.ai.prompt_versions import get_active_prompt_bundle
from app.ai.registry import skill_registry
from app.analytics.routes import token_cost
from app.core.config import settings
from app.core.models import AICallLog
from app.integrations import claude
from eval.exam import runner as exam_runner
from eval.exam.content import app_code_sha256
from eval.exam.env import ExamRefused, LiveDB, Owner

from . import adapters, gold, score

SKILL = "obligation_extraction"
RESULTS_DIR = gold.ROOT / "results" / "scratch"
BASELINES_DIR = gold.ROOT / "results"  # a named baseline is results/baseline-NAME/, committed with the code
BANNER = "PROVISIONAL REHEARSAL - NOT A BASELINE"
MAX_RUNS = 3
BASELINE_RUNS = 2  # two passes: the second says how much of the first was luck
BASELINE_MAX_CEILING_USD = 5.0
_BASELINE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{1,38}[a-z0-9]")

# What the production job puts in "Input payload" (the ids are absent here: the key's text is not a stored contract).
_JOB_INPUT = {"contract_id": None, "contract_version_id": None, "text_snapshot_id": None}

# What an answer is expected to cost, for the estimate only (the ceiling is judged on the worst case, not this):
# a row is about 200 tokens and the audit's strict counts say the model writes roughly one row per atom.
_OUT_TOKENS_BASE = 500
_OUT_TOKENS_PER_ATOM = 200

LIMITS = (
    "Limits: the prompt equals production's minus ids, jurisdiction, value and the three risk lines (risk_band, "
    "risk_counts, risk_note), and the text is the frozen snapshot, so "
    "numbers hold 'under eval conditions' and only like-for-like comparisons mean anything. The keys are AI drafts no "
    "lawyer has confirmed, mostly our own templates, and one atom is a large share of a small contract, so small moves "
    "are noise. A baseline is named only from lawyer-confirmed keys."
)


BASELINE_LIMITS = (
    "Limits: the prompt equals production's minus ids, jurisdiction, value and the three risk lines (risk_band, "
    "risk_counts, risk_note), and the text is the frozen snapshot, so numbers hold 'under eval conditions' and only "
    "like-for-like comparisons mean anything. Mostly our own templates, and one atom is a large share of a small "
    "contract, so small moves are noise: the two passes' difference is the noise to beat. The prompt and the output schema "
    "disagree about fields, the schema cannot hold hours, and a long contract is cut off at the output limit: recorded for "
    "the redesign, not fixed here."
)


@dataclass
class _Item:
    key: gold.Gold
    user_prompt: str
    payload: dict  # the request the client will send, so the estimate prices exactly that


def _prepare(keys: list[gold.Gold], gateway: AIGateway, bundle, spec, ctx: AICallContext) -> tuple[list[_Item], object]:
    """The prompt and request for each key, built as the job builds them. Returns the items and the gateway's plan
    (its model, max_tokens and system prompt).

    ponytail: `_plan` and `_require_structured` are the gateway's own, private, so the request here is the one it will
    send. Make them public if a second caller ever needs them.
    """
    plan = gateway._plan(None, SKILL, ctx, prompt=bundle)
    schema = gateway._require_structured(plan, None)
    tool = plan.feature.tool_name
    items = []
    for key in keys:
        meta = key.metadata
        context = text_only_context(
            text=key.text, title=meta.get("title") or key.title, contract_type=meta.get("contract_type"),
            counterparty_name=meta.get("counterparty_name") or key.parties["them"]["names"][0],
        )
        built = prompt_builder.build_structured_skill_prompt(
            spec=spec, prompt_bundle=bundle, input_payload=_JOB_INPUT, contract_context=context)
        payload = {  # the same shape ClaudeClient.complete_structured builds
            "model": plan.model, "max_tokens": plan.max_tokens, "temperature": plan.feature.temperature,
            "system": claude._cached_system(plan.system_prompt),
            "messages": [{"role": "user", "content": built.user_prompt}],
            "tools": claude._cached_tools([{"name": tool, "description": f"Return structured data for {tool}.",
                                            "input_schema": schema}]),
            "tool_choice": {"type": "tool", "name": tool},
        }
        items.append(_Item(key, built.user_prompt, payload))
    return items, plan


def _estimate(items: list[_Item], plan, runs: int) -> exam_runner.Estimate:
    expected = worst = 0.0
    for item in items:
        sent = len(json.dumps(item.payload, ensure_ascii=False)) // 4
        out = min(plan.max_tokens, _OUT_TOKENS_BASE + _OUT_TOKENS_PER_ATOM * len(item.key.atoms))
        expected += token_cost(sent, out, plan.model)
        worst += exam_runner.worst_cost(item.payload, batched=False)
    calls = len(items) * runs
    return exam_runner.Estimate(
        delivery="sync", calls=calls, batch_calls=0, sync_calls=calls, usd=expected * runs, list_usd=expected * runs,
        worst_case_usd=worst * runs,
        by_call_type={SKILL: {"calls": calls, "batch": 0, "usd": round(expected * runs, 4)}},
    )


async def _extract(live: LiveDB, owner: Owner, run: exam_runner.PaidRun, gateway: AIGateway, bundle, item: _Item,
                   index: int) -> dict:
    """One call for one key: what happened (outcome, tokens, seconds, model) and, when it answered, its rows and
    score. A failed call is a result, not an error; only the ceiling stops the run (`CeilingReached` propagates:
    the meter refused the call before anything left the machine)."""
    ctx = AICallContext(org_id=live.org_id, user_id=owner.user_id, resource=("eval_obligation_key", item.key.id),
                        eval_run_id=run.run_id)
    started = time.perf_counter()
    record = {"key": item.key.id, "run": index}
    try:
        result = await gateway.structured(
            None, SKILL, ctx=ctx, user_prompt=item.user_prompt, prompt=bundle,
            log_input={"key": item.key.id, "run": index, "chars": len(item.key.text)},  # never the text itself
        )
    except exam_runner.CeilingReached:
        raise
    except Exception as exc:
        failure = failure_from_exception(exc)
        print(f"{item.key.id} (run {index + 1}): failed - {failure['outcome']}: {failure['reason']}")
        return {**record, "ok": False, "failure": failure, "seconds": round(time.perf_counter() - started, 1)}
    rows = adapters.rows_from_output(result.data, gold.anchors_for(item.key))
    usage = result.usage
    return {
        **record, "ok": True, "call_id": result.call_id, "model_returned": result.model,
        "stop_reason": result.stop_reason, "usage": usage, "seconds": round(time.perf_counter() - started, 1),
        "usd": round(token_cost(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0, result.model,
                                usage.get("cache_read_input_tokens") or 0,
                                usage.get("cache_creation_input_tokens") or 0), 4),
        "rows": rows, "score": score.score(item.key, rows),
    }


def _forget_refused(live: LiveDB, run_id: str) -> None:
    """A call the ceiling refused never left the machine, so it is not a failed call (the exam forgets it too)."""
    with live.sessions() as db:
        db.execute(delete(AICallLog).where(
            AICallLog.org_id == live.org_id, AICallLog.eval_run_id == run_id,
            AICallLog.error_class == exam_runner.CeilingReached.__name__))
        db.commit()


def _scrub_answers(live: LiveDB, run_id: str) -> None:
    """The ledger keeps what a call cost, not what it said. The gateway stores the validated answer (every quote
    copied from the key's text) and, when AI_STORE_RAW_OUTPUTS is on, the raw reply on the ledger row. This run's rows
    are on the LIVE ledger, where the exam's rule is that no contract text lands; results.json keeps the rows."""
    with live.sessions() as db:
        db.execute(update(AICallLog).where(AICallLog.org_id == live.org_id, AICallLog.eval_run_id == run_id)
                   .values(validated_output=None, raw_ai_output=None))
        db.commit()


def stability(keys: dict[str, gold.Gold], calls: list[dict]) -> dict[str, float | None]:
    """Per key, with two or more answered runs: duties found in every run over duties found in any run."""
    found: dict[str, list[set]] = {}
    for call in calls:
        if call["ok"]:
            headline = {a.id for a in keys[call["key"]].atoms if a.kind in gold.HEADLINE_KINDS}
            found.setdefault(call["key"], []).append(headline - {m["id"] for m in call["score"]["missed"]})
    return {k: (len(set.intersection(*runs)) / len(set.union(*runs)) if set.union(*runs) else None)
            for k, runs in found.items() if len(runs) >= 2}


def render(doc: dict, keys: dict[str, gold.Gold]) -> str:
    """The plain-language page: the stamp first, then every key's scorecard, then the limits."""
    v, actual = doc["versions"], doc["actual"]
    outcomes = Counter("ok" if c["ok"] else c["failure"]["outcome"] for c in doc["calls"])
    confirmed = all(k.confirmed for k in keys.values())
    status = ("KEY STATUS: lawyer-confirmed (" + "; ".join(
        f"{k.id}: {k.status['confirmed_by']}, {k.status['confirmed_at']}" for k in keys.values()) + ")") if confirmed else (
        "KEY STATUS: draft (AI-drafted answer keys that no lawyer has confirmed)")
    lines = [
        doc.get("status") or BANNER,
        status,
        f"run {doc['run_id']}" + (f" - {doc['label']}" if doc["label"] else "") + f" - {doc['started']}",
        f"model requested {v['model_requested']}, returned {', '.join(sorted(set(doc['models_returned']))) or 'none'}",
        f"prompt {v['prompt_hash'][:8]}, output schema {v['schema_hash'][:8]}, app code {v['app_code_sha256'][:8]}",
        f"{actual['calls']} calls, ${actual['usd']:.2f} spent (ceiling ${doc['ceiling_usd']:.2f}); "
        f"outcomes {dict(outcomes)}; run {doc['outcome']}" + (f" ({doc['incomplete_reason']})" if doc["incomplete_reason"] else ""),
        "",
    ]
    for call in doc["calls"]:
        key = keys[call["key"]]
        head = f"run {call['run'] + 1} of {key.id}: "
        if not call["ok"]:
            f = call["failure"]
            lines += [head + f"NO ANSWER - {f['outcome']}: {f['reason']}", ""]
            continue
        cap = doc["max_tokens"]
        out = (call["usage"].get("completion_tokens") or 0)
        lines += [head + f"{call['seconds']}s, {out} of {cap} output tokens ({out / cap:.0%} of the limit), "
                  f"stopped on {call['stop_reason']}, ${call['usd']:.3f}", score.scorecard(call["score"]), ""]
    for key_id, share in stability(keys, doc["calls"]).items():
        lines.append(f"stability {key_id}: {'n/a' if share is None else f'{share:.0%}'} of duties found in any run were "
                     "found in every run")
    return "\n".join([*lines, BASELINE_LIMITS if doc.get("baseline") else LIMITS])


def _save(doc: dict, keys: dict[str, gold.Gold], results_dir: Path) -> tuple[Path, str]:
    folder = results_dir / doc["run_id"]
    folder.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, indent=2, ensure_ascii=False, default=str)
    (folder / "results.json").write_text(body, encoding="utf-8")
    (folder / "scorecard.txt").write_text(render(doc, keys) + "\n", encoding="utf-8")
    return folder, hashlib.sha256(body.encode()).hexdigest()


def _capped(ask: Callable[[str], str], cap: float) -> Callable[[str], str]:
    """The owner's typed ceiling, refused (as if nothing was typed) when it is above `cap`."""
    def capped(prompt: str) -> str:
        typed = ask(prompt)
        # The gate's own parser, not a lookalike: it also drops thousands commas, so "$1,000" is 1000 to it, and a cap
        # that parsed differently would wave that through as "not a number" and approve a $1000 ceiling.
        amount = exam_runner._parse_ceiling(typed)
        if amount is not None and amount > cap:
            print(f"A baseline's ceiling is at most ${cap:g}. Nothing was spent.")
            return ""
        return typed  # approve_paid_run refuses what is not a positive number
    return capped


def _why_not_named(outcome: str, reason: str | None, calls: list[dict], actual: dict, wanted: int) -> str | None:
    """None when this run can be named a baseline, else the plain reason it cannot: every key must have answered in
    both passes, with the ledger holding every call."""
    if outcome != "complete":
        return f"the run did not complete ({reason or outcome})"
    failed = Counter(c["failure"]["outcome"] for c in calls if not c["ok"])
    if failed:
        return f"{sum(failed.values())} call(s) got no answer {dict(failed)}"
    if len(calls) != wanted or actual["calls"] < len(calls):
        return "the ledger does not hold every call"
    return None


def _save_baseline(doc: dict, keys: dict[str, gold.Gold], folder: Path) -> tuple[Path, str]:
    """Write a named baseline: results.json (everything), run-a.json and run-b.json (each pass's calls), scorecard.txt,
    and manifest.json (what it was scored against and the digest of results.json, which `--check` re-verifies). The
    folder must not exist: a baseline is never overwritten."""
    folder.mkdir(parents=True, exist_ok=False)
    body = json.dumps(doc, indent=2, ensure_ascii=False, default=str)
    digest = hashlib.sha256(body.encode()).hexdigest()
    (folder / "results.json").write_text(body, encoding="utf-8")
    for letter, index in (("a", 0), ("b", 1)):
        passes = [c for c in doc["calls"] if c["run"] == index]
        (folder / f"run-{letter}.json").write_text(json.dumps(passes, indent=2, ensure_ascii=False, default=str),
                                                    encoding="utf-8")
    (folder / "scorecard.txt").write_text(render(doc, keys) + "\n", encoding="utf-8")
    manifest = {
        "name": doc["baseline"], "run_id": doc["run_id"], "started": doc["started"], "runs": doc["runs"],
        "results_sha256": digest, "versions": doc["versions"], "ceiling_usd": doc["ceiling_usd"], "actual": doc["actual"],
        "keys": [{"id": k.id, "fingerprint": k.fingerprint, "confirmed_by": k.status["confirmed_by"],
                  "confirmed_at": k.status["confirmed_at"]} for k in keys.values()],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return folder, digest


async def run_obligations(
    *, live: LiveDB, owner: Owner, ask: Callable[[str], str], runs: int = 1, label: str | None = None,
    provider=None, results_dir: Path = RESULTS_DIR, ceiling_from: str = "prompt", baseline: str | None = None,
    baselines_dir: Path = BASELINES_DIR,
) -> dict:
    """The paid ``obligations`` command. Refuses before anything is shown or sent when a guard says no; shows the
    estimate and asks for the typed ceiling; then one call per key per run, each scored against its key.
    ``provider`` is for tests (a fake model); the command passes none and uses the exam's own client.

    With ``baseline`` it is the NAMED baseline instead: refused unless every key is lawyer-confirmed (and unchanged since),
    the run is exactly two passes, the name is new, and the ceiling is at most $5. It is named (written to
    results/baseline-NAME/, to be committed) only if every key answered in both passes; otherwise it is saved as a scratch
    rehearsal and says why it was not named."""
    if not 1 <= runs <= MAX_RUNS:
        raise ExamRefused(f"--runs is 1 to {MAX_RUNS}")
    if baseline is not None:
        if not _BASELINE_NAME.fullmatch(baseline):
            raise ExamRefused("a baseline's name is 3 to 40 lowercase letters, digits or hyphens")
        if runs != BASELINE_RUNS:
            raise ExamRefused(f"a baseline is exactly {BASELINE_RUNS} passes: add --runs {BASELINE_RUNS}")
        if (baselines_dir / f"baseline-{baseline}").exists():
            raise ExamRefused(f"baseline-{baseline} already exists, and a baseline is never overwritten: name a new one")
    if settings.mock_claude:
        raise ExamRefused("MOCK_CLAUDE is on: a fake model's answers would be scored as if real")
    try:
        keys = gold.load_all()
    except gold.GoldError as exc:
        raise ExamRefused(f"an answer key is not usable: {exc}") from exc
    if not keys:
        raise ExamRefused("there are no answer keys under eval/obligations/gold/")
    if baseline is not None:
        loose = [f"{k.id}: {k.status_problem}" for k in keys if not k.confirmed]
        if loose:
            raise ExamRefused("a baseline is named only from lawyer-confirmed keys, and these are not: " + "; ".join(loose))
        ask = _capped(ask, BASELINE_MAX_CEILING_USD)

    spec = skill_registry.get(SKILL)
    gateway = AIGateway(provider=provider or claude.claude_client, ledger_sessions=live.sessions)
    with live.sessions() as db:
        bundle = get_active_prompt_bundle(
            db, org_id=live.org_id, prompt_key=spec.prompt_key, default_version=spec.prompt_version,
            model_config={"temperature": spec.temperature, "max_tokens": spec.max_tokens})
    items, plan = _prepare(keys, gateway, bundle, spec, AICallContext(org_id=live.org_id, user_id=owner.user_id))
    schema_hash = hashlib.sha256(json.dumps(spec.output_model.model_json_schema(), sort_keys=True).encode()).hexdigest()
    versions = {"model_requested": plan.model, "prompt_key": bundle.prompt_key, "prompt_version": bundle.version,
                "prompt_hash": bundle.prompt_hash, "schema_hash": schema_hash, "app_code_sha256": app_code_sha256()}
    print((f"BASELINE {baseline}: {len(keys)} lawyer-confirmed key(s), two passes" if baseline else
           f"{BANNER}: {len(keys)} draft answer key(s)")
          + ": the production extraction prompt, one call per key per run. Nothing is saved as obligations.")
    run = exam_runner.approve_paid_run(
        kind="obligations", live=live, owner=owner, estimate=_estimate(items, plan, runs), ask=ask,
        extra={"label": label, "baseline": baseline, "runs": runs, "ceiling_source": ceiling_from,
               "versions_at_start": {**versions, "model_under_test": plan.model},
               "keys": [{"id": k.id, "fingerprint": k.fingerprint, "status": k.effective_status} for k in keys]},
    )
    if run is None:
        raise ExamRefused("the go-ahead was not given: nothing was sent")
    claude.exam_meter = run.meter

    calls: list[dict] = []
    outcome, reason = "incomplete", "interrupted"
    started = datetime.now(UTC).isoformat(timespec="seconds")
    by_id = {k.id: k for k in keys}
    folder, digest = None, None
    try:
        stopped = False
        # ponytail: one call at a time (a few keys, a few minutes). Add a semaphore when the key set grows; the
        # meter is already thread-safe.
        for index in range(runs):
            for item in items:
                try:
                    calls.append(await _extract(live, owner, run, gateway, bundle, item, index))
                except exam_runner.CeilingReached as exc:
                    print(str(exc))
                    stopped = True
                    break
            if stopped:
                break
        outcome, reason = ("incomplete", "ceiling_reached") if stopped else ("complete", None)
    finally:
        _forget_refused(live, run.run_id)
        _scrub_answers(live, run.run_id)
        actual = exam_runner.ledger_actual(live.sessions, run.run_id, org_id=live.org_id)
        why_not = _why_not_named(outcome, reason, calls, actual, len(keys) * runs) if baseline else "not a baseline run"
        named = baseline is not None and why_not is None
        doc = {
            "status": f"BASELINE {baseline}" if named else (
                f"{BANNER} (NOT NAMED: {why_not})" if baseline else BANNER),
            "baseline": baseline if named else None, "run_id": run.run_id, "label": label, "started": started,
            "runs": runs, "versions": versions, "max_tokens": plan.max_tokens,
            "keys": [{"id": k.id, "fingerprint": k.fingerprint, "status": k.effective_status} for k in keys],
            "estimate": asdict(run.estimate), "ceiling_usd": run.ceiling_usd, "actual": actual, "outcome": outcome,
            "incomplete_reason": reason,
            "models_returned": [c["model_returned"] for c in calls if c["ok"]], "calls": calls,
        }
        if named:
            folder, digest = _save_baseline(doc, by_id, baselines_dir / f"baseline-{baseline}")
        else:
            folder, digest = _save(doc, by_id, results_dir)
        # A call the ledger has no row for (a failed write) must not make a paid run read as free or clean: the
        # meter's own figure is kept beside the ledger's, and fewer rows than calls counts as an error.
        unrecorded = actual["calls"] < len(calls)
        exam_runner.finish_paid_run(
            run, outcome=outcome, reason=reason, actual=actual, results_sha256=digest,
            summary={
                "versions": versions, "contains_errors": actual["failed_calls"] > 0 or unrecorded,
                "failed_call_rate": actual["failed_calls"] / actual["calls"] if actual["calls"] else 0.0,
                "headline": {
                    "label": label, "runs": runs, "keys": len(keys), "provisional": not named,
                    "baseline": baseline if named else None,
                    "metered_usd": round(run.meter.spent_usd, 4) if run.meter is not None else None,
                    "ledger_rows_missing": len(calls) - actual["calls"] if unrecorded else 0,
                    "outcomes": dict(Counter("ok" if c["ok"] else c["failure"]["outcome"] for c in calls)),
                    "recall": {f"{c['key']}#{c['run'] + 1}": c["score"]["recall"] for c in calls if c["ok"]},
                },
            },
        )
    print()
    print(render(doc, by_id))
    if named:
        print(f"\nBaseline {baseline} is named. Saved: {folder} (results.json, run-a.json, run-b.json, scorecard.txt, "
              "manifest.json). Commit that folder with the keys: `--check` re-scores it from those files.")
    else:
        print(f"\nSaved: {folder} (results.json, scorecard.txt). It is a scratch folder: git ignores it."
              + (f"\nThis run is NOT a baseline: {why_not}. Nothing was named; name a new baseline to try again."
                 if baseline else ""))
    return {"run_id": run.run_id, "outcome": outcome, "folder": str(folder), "calls": calls, "named": named}
