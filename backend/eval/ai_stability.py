"""How much does each AI task change its DECISION when asked the same thing three times?

For each task below it takes the N most recent distinct inputs from the AI ledger (a_i_call_log,
status succeeded), rebuilds the exact request today's prompt would send, asks the model R times through
the same gateway path production uses (same system prompt + untrusted-input guard, model, temperature,
forced tool and output schema), and compares the runs on the task's DECISION fields, never on wording
(``COMPARATORS`` below). Tasks are ranked by how much they vary, most variable first.

    docker compose exec -T -e MOCK_CLAUDE=false backend \
        python -m eval.ai_stability --as admin@example.com [--tasks a,b] [--inputs 4] [--runs 3] [--yes]

Without ``--yes`` it prints the plan (inputs found, replays, estimated cost) and spends nothing; it only
READS the ledger and the contract text. With ``--yes`` every replay is a paid call. Refused when
MOCK_CLAUDE is on (a mocked answer proves nothing), when no key is set, or outside local/development.
The report goes to ``eval/results/ai_stability/<date>-<batch>.json``, rewritten after every task so a crash
never loses paid results. Never run by CI.

    python -m eval.ai_stability --from-ledger <batch>[,<batch>...]

scores batches already paid for, from their ledger rows alone: no AI call, no ``--yes``, no key needed. Each
replay row stores ``input_payload.ai_stability = {source_call_id, run}``, so a replay maps back to its input
exactly (the source row also gives the stored answer); several batches pool into one ranking.

Writes: only ledger rows (``resource_type="ai_stability"``, ``resource_id`` = the batch id), through the
gateway. Replays go through ``AIGateway.structured`` directly, NOT ``AIController.run_structured_skill``:
that one also writes skill runs, timeline events, citations and the extracted rows (clauses, obligations...).
The prompt it would build is rebuilt here with the same ``prompt_builder``, so the model sees the same thing.

Replaying "today's prompt against yesterday's input" means the ledger's input must be recoverable. It is
not always (the controller stores a redacted, 500-character-capped copy). Each task has a rebuild step;
an input that cannot be rebuilt is SKIPPED with the reason, which the plan prints. See ``_prepare``.

Not measured, on purpose: Ask Aegis (``assistant_streaming``). Its turn is a tool loop that writes
assistant runs, messages, tool calls and (for draft/extract/confirm tools) contracts, jobs and edits; a
read-only replay would need a second, tool-stubbed loop that is not the production one.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).parent / "results" / "ai_stability"
DEV_ENVIRONMENTS = {"local", "development", "dev"}
RESOURCE_TYPE = "ai_stability"
SCAN_LIMIT = 300  # ledger rows looked at per task while hunting for N replayable distinct inputs
NOT_MEASURED = {
    "assistant_streaming": "Ask Aegis is a tool loop that writes assistant runs, messages, tool calls and "
                           "(draft / extract / confirm tools) contracts, jobs and edits; no read-only replay "
                           "of the production loop exists.",
}


# ---------------------------------------------------------------- normalisers
def norm_text(v: Any) -> str:
    """Case, punctuation and whitespace removed: 'Acme, Inc.' == 'acme inc'."""
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z]+", " ", str(v or "").casefold())).strip()


def norm_date(v: Any) -> str | None:
    """ISO date, or None. '2026-12-30', a date and '2026-12-30T00:00' agree."""
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return norm_text(s) or None


def norm_num(v: Any) -> float | None:
    """1000000, 1000000.0 and '1,000,000' agree; None stays None."""
    if v in (None, ""):
        return None
    try:
        return round(float(str(v).replace(",", "")), 2)
    except ValueError:
        return None


def norm_code(v: Any) -> str | None:
    """An enum-like label (currency, category): upper/lower and spacing removed."""
    return norm_text(v) or None


_STOP = {"obligation", "obligations", "of", "and", "the", "to", "for", "a"}


def norm_type(v: Any) -> str:
    """Obligation type, fuzzy: stop words dropped and tokens sorted, so 'Confidentiality obligations' ==
    'Confidentiality'. # ponytail: still wording-sensitive for synonyms ('Payment' vs 'Fees'); the count atom
    shows when that matters. Upgrade: map to the clause-type list."""
    return " ".join(sorted(t for t in norm_text(v).split() if t not in _STOP))


def norm_party(v: Any) -> str:
    """'The Receiving Party' == 'receiving party'."""
    return " ".join(t for t in norm_text(v).split() if t != "the")


def _js(v: Any) -> str:
    return json.dumps(v, sort_keys=True, default=str)


def _dicts(v: Any) -> list[dict]:
    """The dict members of a list field; anything else (None, a string from an older prompt version) is []."""
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _term(v: Any) -> tuple:
    """A renewal term as (number, unit); an older prompt version stored a plain string: kept as its text."""
    if isinstance(v, dict):
        return (v.get("number"), v.get("unit"))
    return ("raw", norm_text(v)) if v else (None, None)


def safe_decision(fn, output: Any) -> tuple[set | None, str | None]:
    """Run a comparator; an output it cannot read is (None, 'ExcClass: msg'), never an exception. Failure mode:
    one old-shape stored answer crashing a run after 84 paid calls."""
    try:
        if not isinstance(output, dict):
            raise TypeError(f"output is a {type(output).__name__}, not an object")
        return fn(output), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- comparators
# Each takes a task's validated output (dict) and returns the DECISION as a set of hashable atoms:
# ("field", value) for a single answer, ("item", x) for a member of a list. Two runs agree when their
# sets are equal; the Jaccard of the two sets says how close they are when they do not.
def cmp_playbook_review(o: dict) -> set:
    kept = [d for d in _dicts(o.get("deviations")) if d.get("position") != "preferred"]
    out = {("dev", d["rule_index"] if d.get("rule_index") is not None else norm_text(d.get("clause_type")),
            d.get("position") or "outside") for d in kept}
    return out | {("count", len(kept))}


def cmp_intake_triage(o: dict) -> set:
    return {("category", norm_code(o.get("category"))), ("urgency", norm_code(o.get("urgency"))),
            ("complexity", norm_code(o.get("complexity"))), ("risk", norm_code(o.get("risk"))),
            ("needs_info", bool(o.get("missing_info"))), ("needs_human", bool(o.get("needs_human"))),
            ("workflow", o.get("recommended_workflow_id"))}


def cmp_intake_form_read(o: dict) -> set:
    real = [m for m in _dicts(o.get("mismatches")) if not m.get("same") and str(m.get("text_says") or "").strip()]
    return {("n_mismatches", len(real))} | {("field", norm_text(m.get("field"))) for m in real}


def cmp_email_triage(o: dict) -> set:
    return {("is_clm_related", bool(o.get("is_clm_related"))), ("category", norm_code(o.get("category")))}


def cmp_metadata(o: dict) -> set:
    return {("counterparty", norm_text(o.get("counterparty_name")) or None),
            ("effective", norm_date(o.get("effective_date"))), ("expiry", norm_date(o.get("expiration_date"))),
            ("value", norm_num(o.get("value_amount"))), ("currency", norm_code(o.get("currency"))),
            ("governing_law", norm_text(o.get("jurisdiction")) or None)}


def cmp_clause_labeling(o: dict) -> set:
    return {(str(x.get("segment_id")), norm_code(x.get("clause_type"))) for x in _dicts(o.get("labels"))}


def cmp_renewal(o: dict) -> set:
    out = {("term_kind", o.get("term_kind")), ("auto_renews", o.get("auto_renews")),
           ("end_date", norm_date(o.get("stated_end_date"))), ("max_renewals", o.get("max_renewals")),
           ("initial", _term(o.get("initial_term"))), ("renewal_term", _term(o.get("renewal_term")))}
    return out | {("notice", n.get("kind"), n.get("who"), n.get("number"), n.get("unit"))
                  for n in _dicts(o.get("notice_terms"))}


def _due(t: Any) -> tuple:
    """When it falls due, as the contract words it: the written date, else the period + anchor."""
    if not isinstance(t, dict):
        return ("raw", norm_text(t))
    if t.get("explicit_date"):
        return ("date", norm_date(t["explicit_date"]))
    return (t.get("kind"), t.get("period_number"), t.get("period_unit"), t.get("direction"), t.get("anchor"))


def cmp_obligations(o: dict) -> set:
    items = _dicts(o.get("obligations"))
    return {("count", len(items))} | {("ob", norm_type(x.get("obligation_type")),
                                       norm_party(x.get("responsible_party")), _due(x.get("timing") or {}))
                                      for x in items}


def cmp_brain_answer(o: dict) -> set:
    cites = {("cite", norm_text(c.get("label")) or norm_text(c.get("quote"))[:60])
             for c in _dicts(o.get("citations"))}
    return cites | {("contract", str(i)) for i in o.get("related_contract_ids") or []}


def cmp_portfolio_filter(o: dict) -> set:
    skip = {"wording"}  # free-text questions to check: wording by design; only their number is a decision
    out = {(k, _js(sorted(v) if isinstance(v, list) else v)) for k, v in o.items() if k not in skip}
    return out | {("n_wording", len(o.get("wording") or []))}


def cmp_tabular_cell(o: dict) -> set:
    """A yes/no column's typed verdict; otherwise the normalised answer."""
    value = o.get("verdict") or norm_text(o.get("answer"))
    return {("value", value), ("not_found", bool(o.get("not_found")))}


def cmp_edit_suggestions(o: dict) -> set:
    edits = _dicts(o.get("edits"))
    return ({("span", norm_text(e["original_text"])) for e in edits if e.get("original_text")}
            | {("insertions", sum(1 for e in edits if not e.get("original_text")))})


COMPARATORS = {
    "playbook_review": cmp_playbook_review, "intake_triage": cmp_intake_triage,
    "intake_form_read": cmp_intake_form_read, "email_triage_agent": cmp_email_triage,
    "contract_metadata_extraction": cmp_metadata, "clause_labeling": cmp_clause_labeling,
    "renewal_extraction": cmp_renewal, "obligation_extraction": cmp_obligations,
    "contract_brain_answer": cmp_brain_answer, "portfolio_filter": cmp_portfolio_filter,
    "tabular_cell_extraction": cmp_tabular_cell, "contract_edit_suggestions": cmp_edit_suggestions,
}
TASKS = list(COMPARATORS)


# ---------------------------------------------------------------- metric maths
def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def input_metrics(decisions: list[set], original: set | None) -> dict:
    """Per input: all runs identical?, mean pairwise Jaccard, and how many runs equal the stored answer.
    Fewer than 2 runs cannot be scored (stable=None)."""
    if len(decisions) < 2:
        return {"scored": False, "stable": None, "jaccard": None, "runs": len(decisions),
                "matches_original": None, "original_jaccard": None}
    pairs = [jaccard(a, b) for a, b in combinations(decisions, 2)]
    out = {"scored": True, "stable": all(d == decisions[0] for d in decisions),
           "jaccard": sum(pairs) / len(pairs), "runs": len(decisions),
           "matches_original": None, "original_jaccard": None}
    if original is not None:
        out["matches_original"] = sum(1 for d in decisions if d == original)
        out["original_jaccard"] = sum(jaccard(d, original) for d in decisions) / len(decisions)
    return out


def segment_agreement(decisions: list[set]) -> float:
    """clause_labeling: share of segments (any run labelled) that got the same label in EVERY run. A segment
    one run left unlabelled counts as disagreeing."""
    segs = {a[0] for d in decisions for a in d}
    if not segs:
        return 1.0
    agree = sum(1 for s in segs if len({frozenset(a for a in d if a[0] == s) for d in decisions}) == 1)
    return agree / len(segs)


def task_metrics(per_input: list[dict]) -> dict:
    """Roll inputs up to the task: % fully stable, mean Jaccard, % of replays equal to the stored answer."""
    scored = [m for m in per_input if m["scored"]]
    orig = [m for m in scored if m["matches_original"] is not None]
    return {
        "inputs_scored": len(scored),
        "pct_stable": 100 * sum(1 for m in scored if m["stable"]) / len(scored) if scored else None,
        "mean_jaccard": sum(m["jaccard"] for m in scored) / len(scored) if scored else None,
        "pct_replays_match_original": (100 * sum(m["matches_original"] for m in orig)
                                       / sum(m["runs"] for m in orig)) if orig else None,
        "replays_planned": sum(m.get("planned", 0) for m in per_input),
        "replays_failed": sum(m.get("failed", 0) for m in per_input),
        "replays_truncated": sum(m.get("truncated", 0) for m in per_input),
        "replays_uncomparable": sum(m.get("uncomparable", 0) for m in per_input),
    }


def rank(tasks: dict[str, dict]) -> list[str]:
    """Most variable first: lowest % stable, then lowest Jaccard. Unscored tasks go last."""
    def key(t):
        m = tasks[t]
        return (m["pct_stable"] is None, m["pct_stable"] or 0, m["mean_jaccard"] or 0)
    return sorted(tasks, key=key)


# ---------------------------------------------------------------- inputs from the ledger
@dataclass
class Item:
    task: str
    call_id: str
    payload_hash: str
    stored_output: dict | None
    stored_version: str | None
    model: str | None
    tokens: tuple[int, int]
    user_prompt: str = ""
    schema: dict | None = None
    bundle: Any = None
    notes: list[str] = field(default_factory=list)


class Skip(Exception):
    """This ledger row cannot be replayed against today's prompt; the message says why."""


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(_js(payload).encode()).hexdigest()[:16]


_MARKERS = ("<truncated length=", "<redacted")


def _has_marker(payload: Any) -> bool:
    return any(m in _js(payload) for m in _MARKERS)


def _rebuild_edit_instructions(db, org_id: str, payload: dict) -> str:
    """The ledger kept 500 characters of the edit request. The full text is the sentence built from the
    playbook issue (``issue_fixes.fix_instruction``): find the issue on that contract whose sentence has the
    stored start and the stored length. No match = it was typed by hand = unrecoverable."""
    from sqlalchemy import select

    from app.contract_files.issue_fixes import fix_instruction
    from app.playbooks.models import PlaybookDeviation

    text = str(payload.get("instructions") or "")
    m = re.fullmatch(r"(.*)<truncated length=(\d+)>", text, re.DOTALL)
    if not m:
        if _has_marker(text):
            raise Skip("the edit request holds redacted text")
        return text
    prefix, length = m.group(1), int(m.group(2))
    for dev in db.scalars(select(PlaybookDeviation).where(
            PlaybookDeviation.contract_id == payload.get("contract_id"), PlaybookDeviation.org_id == org_id)):
        full = fix_instruction(dev)
        if len(full) == length and full.startswith(prefix):
            return full
    raise Skip("the edit request was stored cut at 500 characters and no playbook issue rebuilds it")


def _skill_prompt(db, task: str, org_id: str, payload: dict):
    """The user turn AIController.run_structured_skill would build, minus its writes."""
    from app.ai.context import build_contract_context
    from app.ai.gateway import ai_gateway
    from app.ai.prompt_builder import prompt_builder
    from app.ai.registry import skill_registry

    spec = skill_registry.get(task)
    bundle = ai_gateway.prompt_for(db, task, org_id=org_id)
    ctx = None
    if payload.get("contract_id"):
        try:
            ctx = build_contract_context(
                db, user=None, org_id=org_id, contract_id=payload["contract_id"],
                contract_version_id=payload.get("contract_version_id"),
                text_snapshot_id=payload.get("text_snapshot_id"), focus_query=payload.get("focus_query"),
                skill_name=spec.name)
        except Exception as exc:  # contract deleted, version gone
            raise Skip(f"the contract text can no longer be read ({type(exc).__name__})") from exc
    built = prompt_builder.build_structured_skill_prompt(
        spec=spec, prompt_bundle=bundle, input_payload=payload, contract_context=ctx)
    return built.user_prompt, bundle


def _prepare(db, task: str, org_id: str, row) -> Item:
    """Rebuild one ledger row into a request, or raise Skip(reason)."""
    payload = row.input_payload
    item = Item(task=task, call_id=row.id, payload_hash=payload_hash(payload), stored_output=row.validated_output,
                stored_version=row.prompt_version, model=row.model,
                tokens=(row.prompt_tokens or 0, row.completion_tokens or 0))
    if not isinstance(payload, dict) or not payload:
        raise Skip("no input was stored")
    from app.ai.gateway import ai_gateway

    if task in ("intake_triage", "intake_form_read", "email_triage_agent"):
        # Standalone agents store their whole prompt, unredacted.
        up = payload.get("user_prompt")
        if not isinstance(up, str) or not up.strip():
            raise Skip("no prompt was stored")
        if task == "intake_triage":
            from app.intake.triage_agent import _SCHEMA as schema
        elif task == "intake_form_read":
            from app.core.config import settings
            from app.core.database import utcnow
            from app.intake.triage_agent import _READ_SCHEMA as schema
            # aegis_read puts today's date and the currency note in front of the stored part.
            up = (f"Today is {utcnow().date().isoformat()}. Form amounts are in "
                  f"{settings.default_currency} unless a field says otherwise.\n\nINTAKE REQUEST:" + up)
            item.notes.append("prompt starts with today's date, as in production")
        else:
            from app.intake.email_triage_agent import _SCHEMA as schema
        item.user_prompt, item.schema = up, schema
        item.bundle = ai_gateway.prompt_for(db, task, org_id=org_id)
        return item

    if task in ("contract_brain_answer",):
        raise Skip("the retrieved context was stored cut at 500 characters; the sources cannot be rebuilt")
    check = {k: v for k, v in payload.items() if k not in ("rules", "instructions", "focus_query")}
    if _has_marker(check):
        raise Skip("the stored input was cut or redacted by the ledger")
    payload = dict(payload)
    if task == "playbook_review":
        if row.resource_type != "contract":
            raise Skip(f"reviewed {row.resource_type} text, which is not stored")
        from app.playbooks.service import rule_payloads_for_review
        payload["rules"] = rule_payloads_for_review(db, org_id=org_id, playbook_version_id=payload["playbook_version_id"])
        if not payload["rules"]:
            raise Skip("the playbook version has no rules now")
        item.notes.append("rules rebuilt from the stored playbook version")
    elif task == "contract_edit_suggestions":
        payload["instructions"] = payload["focus_query"] = _rebuild_edit_instructions(db, org_id, payload)
        item.notes.append("request rebuilt from its playbook issue; contract text is the current version")
    elif task == "tabular_cell_extraction":
        item.notes.append("contract text is the current version (the ledger did not store which)")
    item.user_prompt, item.bundle = _skill_prompt(db, task, org_id, payload)
    return item


def collect(db, org_id: str, task: str, n: int) -> tuple[list[Item], Counter]:
    """The n most recent distinct, replayable inputs for a task, and why others were skipped."""
    from sqlalchemy import select

    from app.core.models import AICallLog

    rows = db.scalars(select(AICallLog).where(
        AICallLog.org_id == org_id, AICallLog.prompt_key == task, AICallLog.status == "succeeded",
        AICallLog.resource_type.is_distinct_from(RESOURCE_TYPE)
    ).order_by(AICallLog.created_at.desc()).limit(SCAN_LIMIT))
    items, seen, skipped = [], set(), Counter()
    for row in rows:
        h = payload_hash(row.input_payload)
        if h in seen:
            continue
        seen.add(h)
        try:
            items.append(_prepare(db, task, org_id, row))
        except Skip as why:
            skipped[str(why)] += 1
        except Exception as exc:  # a broken row must not stop the plan
            skipped[f"could not be rebuilt ({type(exc).__name__}: {exc})"] += 1
        if len(items) >= n:
            break
    return items, skipped


# ---------------------------------------------------------------- plan
def estimate(items: list[Item], runs: int) -> float:
    from app.analytics.routes import token_cost

    return sum(token_cost(i.tokens[0], i.tokens[1], i.model) * runs for i in items)


def refusals(settings) -> list[str]:
    out = []
    if settings.mock_claude:
        out.append("MOCK_CLAUDE is on: a mocked answer proves nothing. Run with `-e MOCK_CLAUDE=false`.")
    if not settings.claude_api_key:
        out.append("No Claude API key is set.")
    if settings.environment.lower() not in DEV_ENVIRONMENTS:
        out.append(f"Environment is {settings.environment!r}; this runs only in local/development.")
    return out


def print_plan(found: dict[str, tuple[list[Item], Counter]], runs: int) -> None:
    from app.core.config import settings

    print(f"AI stability plan: each input asked {runs} times; replays go through the gateway, ledger rows only")
    print(f"{'task':32}{'inputs':>7}{'replays':>9}{'est. cost':>11}")
    total_replays, total_cost = 0, 0.0
    for task, (items, skipped) in found.items():
        cost = estimate(items, runs)
        total_replays, total_cost = total_replays + len(items) * runs, total_cost + cost
        print(f"{task:32}{len(items):>7}{len(items) * runs:>9}{f'${cost:.2f}':>11}")
        for why, count in skipped.most_common():
            print(f"    skipped {count}: {why}")
        for note in sorted({n for i in items for n in i.notes}):
            print(f"    note: {note}")
        changed = sum(1 for i in items if i.stored_version and i.bundle and i.stored_version != i.bundle.version)
        if changed:
            print(f"    note: {changed} input(s) were originally answered with an older prompt version")
    for task, why in NOT_MEASURED.items():
        print(f"{task:32}  not measured: {why}")
    print(f"{'TOTAL':32}{'':>7}{total_replays:>9}{f'${total_cost:.2f}':>11}   (stored average tokens x price table)")
    problems = refusals(settings)
    if problems:
        print("A paid run would be refused now:\n  " + "\n  ".join(problems))
    print("Nothing was run. Add --yes to spend it.")


# ---------------------------------------------------------------- the paid run
async def replay_once(db, user, item: Item, batch: str, n: int) -> dict:
    """One call, as production makes it. Returns the validated output as a dict."""
    from pydantic import BaseModel

    from app.ai.gateway import AICallContext, ai_gateway

    result = await ai_gateway.structured(
        db, item.task, ctx=AICallContext(org_id=user.org_id, user_id=user.id, resource=(RESOURCE_TYPE, batch)),
        user_prompt=item.user_prompt, input_schema=item.schema, prompt=item.bundle,
        log_input={"ai_stability": {"source_call_id": item.call_id, "run": n}})
    return result.data.model_dump(mode="json") if isinstance(result.data, BaseModel) else result.data


def is_truncated(reason: str) -> bool:
    """A failed replay whose reason says the answer hit the output cap (live message 'cut off at max_tokens',
    ledger validation_error 'truncated: ...')."""
    low = reason.lower()
    return "max_tokens" in low or low.startswith("truncated")


def score_input(task: str, source_call_id: str, stored_output: Any, stored_version: str | None, notes: list[str],
                outputs: list[Any], errs: list[str], planned: int) -> tuple[dict, dict]:
    """Metrics + report entry for one input. Failure mode: a comparator raising on an odd stored or replayed
    answer. Such an output is 'uncomparable' (named by exception class) and the run goes on."""
    cmp_fn = COMPARATORS[task]
    decisions, bad = [], []
    for o in outputs:
        d, err = safe_decision(cmp_fn, o)
        decisions.append(d) if err is None else bad.append(err)
    original, stored_bad = (safe_decision(cmp_fn, stored_output) if stored_output else (None, None))
    m = input_metrics(decisions, original)
    m.update(planned=planned, failed=len(errs), truncated=sum(1 for e in errs if is_truncated(e)),
             uncomparable=len(bad))
    if task == "clause_labeling" and len(decisions) > 1:
        m["segment_agreement"] = segment_agreement(decisions)
    both = set.intersection(*decisions) if decisions else set()
    entry = {"source_call_id": source_call_id, "stored_prompt_version": stored_version, "notes": notes,
             "errors": errs, "uncomparable": bad, "stored_uncomparable": stored_bad, "metrics": m,
             "stored_decision": sorted(map(_js, original or [])),
             "replay_decisions": [sorted(map(_js, d)) for d in decisions],
             "disagreeing_atoms": sorted(map(_js, set().union(*decisions) - both)) if decisions else []}
    return m, entry


def finish_task(report: dict, task: str, per_input: list[dict], task_json: dict) -> None:
    task_json["metrics"] = task_metrics(per_input)
    if task == "clause_labeling":
        seg = [m["segment_agreement"] for m in per_input if "segment_agreement" in m]
        task_json["metrics"]["mean_segment_agreement"] = sum(seg) / len(seg) if seg else None
    report["tasks"][task] = task_json


def write_report(path: Path, report: dict, complete: bool) -> None:
    """Rewritten after every task: a crash later in the run keeps everything already paid for."""
    summary = {t: j["metrics"] for t, j in report["tasks"].items()}
    report["ranking"] = rank(summary)
    report["errors"] = sum(j["metrics"]["replays_failed"] for j in report["tasks"].values())
    report["complete"] = complete
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str))


async def run_all(args, found: dict[str, tuple[list[Item], Counter]], db, user) -> int:
    batch = str(uuid.uuid4())
    report: dict[str, Any] = {"date": date.today().isoformat(), "batch": batch, "as": args.as_user,
                              "runs": args.runs, "tasks": {}, "not_measured": NOT_MEASURED}
    path = RESULTS / f"{report['date']}-{batch[:8]}.json"
    print(f"Batch {batch}\nReport (rewritten after each task): {path}")
    for task, (items, skipped) in found.items():
        per_input, task_json = [], {"skipped": dict(skipped), "inputs": []}
        for item in items:
            outputs, errs = [], []
            for n in range(1, args.runs + 1):
                try:
                    outputs.append(await replay_once(db, user, item, batch, n))
                    print(f"  {task} {item.call_id[:8]} run {n}: ok")
                except Exception as exc:  # one failing replay never stops the others
                    errs.append(f"{type(exc).__name__}: {exc}")
                    print(f"  {task} {item.call_id[:8]} run {n}: FAILED {type(exc).__name__}")
                finally:
                    db.rollback()  # nothing here is ever committed
            m, entry = score_input(task, item.call_id, item.stored_output, item.stored_version, item.notes,
                                   outputs, errs, args.runs)
            per_input.append(m)
            task_json["inputs"].append(entry)
        finish_task(report, task, per_input, task_json)
        write_report(path, report, complete=False)
    write_report(path, report, complete=True)
    _print_ranking(report)
    print(f"Report: {path}\nReplay errors: {report['errors']}")
    return 1 if report["errors"] else 0


# ---------------------------------------------------------------- scoring from the ledger
def tool_input_of(raw: Any, tool_name: str | None = None) -> dict | None:
    """The forced tool's input out of a stored provider response (``raw_ai_output``); None if there is none.
    Looks for a ``tool_use`` block in ``content`` / ``tool_use_blocks`` at the top or under one wrapper key."""
    if isinstance(raw, list):
        blocks = raw
    elif isinstance(raw, dict):
        blocks = raw.get("tool_use_blocks") or raw.get("content") or []
        if not blocks:
            for v in raw.values():
                if isinstance(v, dict) and (found := tool_input_of(v, tool_name)) is not None:
                    return found
    else:
        return None
    for b in blocks if isinstance(blocks, list) else []:
        if isinstance(b, dict) and isinstance(b.get("input"), dict) and (
                b.get("type") in (None, "tool_use")) and (tool_name is None or b.get("name") in (None, tool_name)):
            return b["input"]
    return None


def replay_outcome(row) -> tuple[Any, str | None]:
    """(output, None) for a replay row that answered, (None, reason) for one that failed. Validated output
    first; a row without it (``not_validated`` standalone agents) is parsed from the stored tool call. A
    truncated or invalid row is a FAILED run with the ledger's own reason, e.g. 'truncated: answer cut off...'."""
    if row.status == "succeeded":
        out = row.validated_output if row.validated_output is not None else tool_input_of(row.raw_ai_output)
        return (out, None) if out is not None else (None, "succeeded row holds no readable output")
    reason = row.validation_error or row.error_class or f"status {row.status}"
    if row.stop_reason == "max_tokens" and not is_truncated(reason):
        reason = f"truncated: {reason}"
    return None, reason


def score_from_ledger(db, org_id: str, batches: list[str]) -> dict:
    """Rebuild the replay outputs of paid batches from a_i_call_log (no AI call) and score them like a live run.
    Mapping is exact: every replay row stored ``source_call_id`` + ``run`` in input_payload.ai_stability, and
    the source row (prompt_key, stored answer, prompt version) is read back by that id. Inputs are keyed
    (batch, source id), so the same input in two batches counts as two inputs rather than a doubled run."""
    from sqlalchemy import select

    from app.core.models import AICallLog

    rows = list(db.scalars(select(AICallLog).where(
        AICallLog.org_id == org_id, AICallLog.resource_type == RESOURCE_TYPE, AICallLog.resource_id.in_(batches)
    ).order_by(AICallLog.created_at)))
    by_input: dict[tuple, list] = {}
    for r in rows:
        src = ((r.input_payload or {}).get("ai_stability") or {}).get("source_call_id")
        if src:
            by_input.setdefault((r.resource_id, src, r.prompt_key), []).append(r)
    sources = {r.id: r for r in db.scalars(select(AICallLog).where(
        AICallLog.id.in_({k[1] for k in by_input})))}
    report: dict[str, Any] = {"date": date.today().isoformat(), "batch": ",".join(batches), "source": "ledger",
                              "tasks": {}, "not_measured": NOT_MEASURED}
    per_task: dict[str, tuple[list, dict]] = {}
    for (_, src, task), rs in by_input.items():
        if task not in COMPARATORS:
            continue
        outputs, errs = [], []
        for r in rs:
            out, why = replay_outcome(r)
            outputs.append(out) if why is None else errs.append(why)
        source = sources.get(src)
        m, entry = score_input(task, src, source.validated_output if source else None,
                               source.prompt_version if source else None,
                               ["scored from the ledger"] + ([] if source else ["source row is gone"]),
                               outputs, errs, len(rs))
        per_input, tj = per_task.setdefault(task, ([], {"skipped": {}, "inputs": []}))
        per_input.append(m)
        tj["inputs"].append(entry)
    for task, (per_input, tj) in per_task.items():
        finish_task(report, task, per_input, tj)
    return report


def _print_ranking(report: dict) -> None:
    f = lambda v, fmt: "n/a" if v is None else fmt % v
    print(f"\nMost variable first\n{'task':32}{'inputs':>7}{'% stable':>10}{'jaccard':>9}{'= stored':>10}"
          f"{'replays':>9}{'failed':>8}{'trunc':>7}{'uncomp':>8}")
    for t in report["ranking"]:
        m = report["tasks"][t]["metrics"]
        print(f"{t:32}{m['inputs_scored']:>7}{f(m['pct_stable'], '%.0f%%'):>10}{f(m['mean_jaccard'], '%.2f'):>9}"
              f"{f(m['pct_replays_match_original'], '%.0f%%'):>10}{m['replays_planned']:>9}{m['replays_failed']:>8}"
              f"{m['replays_truncated']:>7}{m['replays_uncomparable']:>8}")
    for t in report["ranking"]:
        m = report["tasks"][t]["metrics"]
        if m["replays_failed"]:
            print(f"  {t}: failed: {m['replays_failed']}, of which truncated: {m['replays_truncated']}")


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as", dest="as_user", default="admin@example.com", help="active user who is accountable")
    ap.add_argument("--tasks", help=f"comma-separated subset of: {', '.join(TASKS)}")
    ap.add_argument("--inputs", type=int, default=4, help="distinct inputs per task (default 4)")
    ap.add_argument("--runs", type=int, default=3, help="replays per input (default 3)")
    ap.add_argument("--yes", action="store_true", help="really spend: without it only the plan is printed")
    ap.add_argument("--from-ledger", metavar="BATCH[,BATCH]",
                    help="score already-paid batches from the ledger (no AI call, no --yes needed)")
    args = ap.parse_args(argv)

    tasks = [t for t in (args.tasks.split(",") if args.tasks else TASKS) if t]
    bad = [t for t in tasks if t not in COMPARATORS]
    if bad:
        ap.error(f"unknown task(s): {', '.join(bad)}")
    if args.runs < 2 or args.inputs < 1:
        ap.error("--runs must be at least 2 and --inputs at least 1")

    from sqlalchemy import select

    import app.models  # noqa: F401  (registers every ORM model)
    from app.auth.models import User
    from app.core.config import settings
    from app.core.database import SessionLocal

    if args.from_ledger:
        batches = [b for b in args.from_ledger.split(",") if b]
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.email == args.as_user))
            if user is None:
                print(f"--as {args.as_user!r} is not a user.")
                return 2
            report = score_from_ledger(db, user.org_id, batches)
            db.rollback()
        if not report["tasks"]:
            print("No ai_stability ledger rows found for that batch.")
            return 2
        path = RESULTS / f"{report['date']}-ledger-{batches[0][:8]}.json"
        write_report(path, report, complete=True)
        _print_ranking(report)
        print(f"Report: {path}")
        return 0
    if args.yes and (problems := refusals(settings)):
        print("Refusing to run:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == args.as_user))
        if user is None or not getattr(user, "is_active", True):
            print(f"--as {args.as_user!r} is not an active user.")
            return 2
        found = {t: collect(db, user.org_id, t, args.inputs) for t in tasks}
        db.rollback()
        if not args.yes:
            print_plan(found, args.runs)
            return 0
        return asyncio.run(run_all(args, found, db, user))


if __name__ == "__main__":
    sys.exit(main())
