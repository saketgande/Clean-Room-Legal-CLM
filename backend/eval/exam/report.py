"""Reports for the AI exam: the owner's private detail file, the public numbers-only report, and
the check that two runs may stand together as a pair.

specs/005-ai-eval-baseline T014 (FR-26, FR-27, FR-30, FR-36, FR-40, FR-41; D-12, D-13). Nothing
here touches a database, the network or a model.

``write_detail`` writes ``detail.md`` into the run's private folder. It may quote contract text
(invalid quotes, missed findings), so it refuses to be written anywhere but under
``eval/private/runs``.

``build_report`` turns a ``score.score_pair`` result (and, for a change, the ``score.verdict``)
into the committed report object; ``write_public_report`` renders it as ``<stem>.md`` +
``<stem>.json`` in ``reports/``. A public report holds scores, counts, ids and rule / field /
workflow names only. It is refused when it carries a private key, an owner file name, or any
eight consecutive words of an owner contract's frozen text -- the owner's contracts must never
reach version control (FR-36).

``check_pair`` says whether two finished runs may be designated a baseline (FR-27). The finished
record it reads is the ``after`` of the run's ``eval.run_finished`` audit row with the row's
``resource_id`` added as ``run_id``.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from eval.exam import score
from eval.exam.content import EXAM_DIR, OWNER_FILES, PRIVATE_DIR, REPORTS_DIR, ExamContent
from eval.exam.score import COST_TIME, HEADLINE, REPORT_ONLY

REPORT_SCHEMA = "aegis-exam-report/1"

# Runner.RUNS_DIR names the same folder; it cannot be imported here because the runner imports
# this module (and the app's database engine along with it).
RUNS_DIR = PRIVATE_DIR / "runs"
OWNER_TEXTS_DIR = PRIVATE_DIR / "owner" / "texts"
SUPERSEDED_DIR = EXAM_DIR.parent / "results" / "superseded"

# D-13: a run in which more than this share of AI calls failed cannot be part of a baseline.
MAX_FAILED_CALL_RATE = 0.01

# The plain-language line for the missed-findings list, which is not a score and so has no entry in
# score.SPECS (FR-26 asks for n and a plain line on it like on every measure).
MISSED_HIGH_CRITICAL_EXPLANATION = (
    "High and critical findings the AI failed to flag, named so that the worst misses are not "
    "lost inside an average; n is how many such findings the answer keys hold in the review "
    "documents that were scored."
)

SAMPLE_LABEL = "SAMPLE — not comparable to the baseline"
SUPERSEDED_LABEL = "earlier, superseded"

# This many consecutive words shared with an owner contract's text and the report is refused.
SHINGLE_WORDS = 8

# Keys that only ever hold contract text or answers; a public report must not contain one.
_PRIVATE_KEYS = frozenset(
    {"private", "quote", "quotes", "text", "invalid_quotes", "failure_reasons", "signatures"}
)

_WORDS = re.compile(r"\w+")
_STEM = re.compile(r"[\w][\w.-]*")
_RUN_ID = re.compile(r"[0-9A-Za-z-]{8,64}")

_DELIVERY = {
    "batch": "batch (discounted: results come back later, priced at half)",
    "sync": "sync (every call sent normally)",
}


class ReportRefused(ValueError):
    """A report that must not be written (it would leak private text, or overwrite a record)."""


# --- small helpers ----------------------------------------------------------------------


def _fmt(key: str, value) -> str:
    """A measure's number the way it reads: dollars, seconds, tokens, or a 0-1 score."""
    if value is None:
        return "—"
    if key in ("cost_usd", "list_cost_usd"):
        return f"${value:,.2f}"
    if key == "total_tokens":
        return f"{value:,.0f}"
    if key in ("wall_time_s", "batch_turnaround_s"):
        return f"{value:,.0f} s"
    if key == "latency_ms_avg":
        return f"{value:,.0f} ms"
    return f"{value:.3f}"


def _band(key: str, value) -> str:
    return "—" if value is None else f"±{_fmt(key, value)}"


def _cell(value, limit: int = 400) -> str:
    text = "—" if value is None else re.sub(r"\s+", " ", str(value)).strip()
    text = text.replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _table(headers: str, rows: list[list]) -> list[str]:
    """A markdown table; ``headers`` is one string, column names separated by ``|``."""
    if not rows:
        return []
    names = headers.split("|")
    out = ["| " + " | ".join(names) + " |", "|" + "---|" * len(names)]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return [*out, ""]


def _explained(m: Mapping) -> str:
    """A measure's plain-language line, with "lower is better" added once where it applies."""
    lower = m["direction"] == "lower" and "lower is better" not in m["explanation"]
    return m["explanation"] + (" (lower is better)" if lower else "")


def _listed(counts: Mapping) -> str:
    """{"public": 40, "owner": 14} as "public 40, owner 14"."""
    return ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items()) or "none"


def _short(value, size: int = 8) -> str:
    return str(value)[:size] if value else "—"


def _sha(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def run_flags(run: Mapping) -> list[str]:
    """The flags a run carries in every report: sample, contains errors, incomplete (FR-30, FR-40).

    "contains errors" is shown whenever any AI call failed, even under the 1% a baseline allows."""
    flags = []
    if run.get("mode") == "sample":
        flags.append("sample")
    if run.get("contains_errors") or (run.get("failed_call_rate") or 0) > 0:
        flags.append("contains errors")
    if run.get("outcome") != "complete":
        flags.append("incomplete")
    return flags


def _banner(runs: list[Mapping], names: tuple[str, ...] = ("Run A", "Run B")) -> list[str]:
    lines = []
    if any(r.get("mode") == "sample" for r in runs):
        lines.append(f"**{SAMPLE_LABEL}.**")
    for name, run in zip(names, runs):
        flags = [f for f in run_flags(run) if f != "sample"]
        if flags:
            rate = run.get("failed_call_rate")
            detail = f" ({rate:.1%} of AI calls failed)" if rate else ""
            lines.append(f"{name}: **{' and '.join(flags)}**{detail}.")
    return [*lines, ""] if lines else []


# --- check_pair: may these two runs be a baseline? (FR-27, D-13, FR-41) -----------------


def _results_sha(run_id: str) -> str | None:
    path = RUNS_DIR / run_id / "results.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _problems_with(name: str, record: Mapping | None) -> list[str]:
    who = f"Run {name}"
    if not record:
        return [f"{who} has no finished-run record (eval.run_finished)"]
    run_id = str(record.get("run_id") or "")
    who = f"Run {name} ({_short(run_id)})"
    problems = []
    if record.get("kind") != "exam":
        problems.append(f"{who} is not an exam run (it is a '{record.get('kind')}' run)")
    if record.get("mode") != "full":
        problems.append(
            f"{who} is a {record.get('mode') or 'unknown-mode'} run; only full runs count"
        )
    if record.get("outcome") != "complete":
        why = record.get("incomplete_reason")
        problems.append(
            f"{who} did not complete (outcome: {record.get('outcome')}"
            + (f", reason: {why}" if why else "")
            + ")"
        )
    rate = record.get("failed_call_rate")
    if rate is None:
        problems.append(f"{who} did not record how many of its AI calls failed")
    elif rate > MAX_FAILED_CALL_RATE:
        problems.append(
            f"{who} had {rate:.1%} of its AI calls fail; more than "
            f"{MAX_FAILED_CALL_RATE:.0%} cannot form a baseline"
        )
    if not _RUN_ID.fullmatch(run_id):
        return [*problems, f"{who} has no usable run id, so its results file cannot be checked"]
    recorded = record.get("results_sha256")
    actual = _results_sha(run_id)
    if not recorded:
        problems.append(f"{who} has no results hash in its record")
    elif actual is None:
        problems.append(f"{who}: its results file is missing")
    elif actual != recorded:
        problems.append(f"{who}: its results file has changed since the run finished")
    return problems


def _prompts(versions: Mapping) -> list:
    return sorted(
        (str(p.get("prompt_key")), str(p.get("version")), str(p.get("hash")))
        for p in versions.get("prompts") or []
    )


# What two runs must share to be a pair: the code, the models, the prompts, the exam content and
# how the calls were delivered. (what it is called, how to read it off a finished record)
_MUST_MATCH = (
    ("delivery mode", lambda r: r.get("delivery")),
    ("app code version", lambda r: (r.get("versions") or {}).get("app_code_sha256")),
    ("models", lambda r: sorted((r.get("versions") or {}).get("models") or [])),
    ("prompt versions", lambda r: _prompts(r.get("versions") or {})),
    ("exam content", lambda r: (r.get("versions") or {}).get("content_fingerprint")),
)


def check_pair(a_finished: dict, b_finished: dict) -> list[str]:
    """Everything wrong with offering these two runs as a baseline (or as a change's two runs);
    an empty list means they are fit to be a pair (FR-27, D-13, FR-41).

    A pair is two different full, complete exam runs, each with at most 1% failed AI calls, whose
    results files are the ones their audit records describe, on the same delivery mode, app code,
    models, prompt versions and exam content. Each argument is a finished-run record: the ``after``
    of the run's ``eval.run_finished`` row plus ``run_id`` (the row's ``resource_id``); None when
    the run has no such row.
    """
    problems = _problems_with("A", a_finished) + _problems_with("B", b_finished)
    if not a_finished or not b_finished:
        return problems
    if a_finished.get("run_id") and a_finished.get("run_id") == b_finished.get("run_id"):
        problems.append("Run A and run B are the same run; a noise band needs two different runs")
    for what, read in _MUST_MATCH:
        a, b = read(a_finished), read(b_finished)
        if not a or not b:
            problems.append(f"the two runs must share the {what}, but it was not recorded")
        elif a != b:
            problems.append(f"the two runs differ in {what}")
    return problems


# --- the public report: built from a pair, checked, then written ------------------------


def superseded_history() -> list[dict]:
    """The earlier branch results, kept as history under their own label (FR-29)."""
    if not SUPERSEDED_DIR.is_dir():
        return []
    return [
        {"file": f"superseded/{p.name}", "label": SUPERSEDED_LABEL}
        for p in sorted(SUPERSEDED_DIR.glob("*.json"))
    ]


def _bands(a, b) -> dict:
    """Two runs' values with their average and the noise band (how far apart they are)."""
    both = None not in (a, b)
    return {
        "run_a": a,
        "run_b": b,
        "average": (a + b) / 2 if both else None,
        "noise_band": abs(a - b) if both else None,
    }


def _by_field(scores: Mapping) -> dict:
    """FR-26: each extraction field's accuracy, overall and before / past 25,000 characters."""

    def flat(side: Mapping | None) -> dict:
        if not side:
            return dict.fromkeys(("all", "before_25k", "past_25k"), (0, None))
        return {
            "all": (side["n"], side["accuracy"]),
            **{b: (side[b]["n"], side[b]["value"]) for b in ("before_25k", "past_25k")},
        }

    fields_a, fields_b = scores["a"]["fields"], scores["b"]["fields"]
    out = {}
    for name in dict.fromkeys([*fields_a, *fields_b]):
        a, b = flat(fields_a.get(name)), flat(fields_b.get(name))
        out[name] = {k: {"n": min(a[k][0], b[k][0]), **_bands(a[k][1], b[k][1])} for k in a}
    return out


def build_report(
    pair: dict,
    *,
    kind: Literal["baseline", "comparison"],
    label: str,
    comparison: dict | None = None,
) -> dict:
    """The committed report object for a ``score.score_pair`` result.

    ``comparison`` is ``score.verdict(...)`` for a change's pair and None for a baseline. Only
    numbers, ids and names are copied out of the pair; its ``private`` block (quote text, answers)
    is read for counts alone.
    """
    if kind not in ("baseline", "comparison"):
        raise ValueError(f"report kind must be 'baseline' or 'comparison', got '{kind}'")
    scores = pair["private"]["scores"]
    runs = []
    for side, run in zip("ab", pair["runs"]):
        versions = run.get("versions") or {}
        runs.append(
            {
                "run_id": run.get("run_id"),
                "mode": run.get("mode"),
                "delivery": run.get("delivery"),
                "outcome": run.get("outcome"),
                "contains_errors": run.get("contains_errors"),
                "failed_call_rate": run.get("failed_call_rate"),
                "flags": run_flags(run),
                "change": run.get("change"),
                "target_area": run.get("target_area"),
                "versions": {
                    "app_code_sha256": versions.get("app_code_sha256"),
                    "model_under_test": versions.get("model_under_test"),
                    "models": versions.get("models"),
                    "prompts_sha256": _sha(_prompts(versions)),
                },
                "cost_usd": run.get("cost_usd"),
                "estimate_usd": run.get("estimate_usd"),
                "wall_time_s": run.get("wall_time_s"),
                "cost_by_call_type": scores[side]["cost_vs_estimate"]["by_call_type"],
                # FR-40: failures are counted and listed, by id and short reason only.
                "failed_calls": scores[side]["counts"]["failed_calls"],
                "failed": {
                    "calls": scores[side]["failed_calls"],
                    "items": scores[side]["failed_items"],
                },
            }
        )
    c = scores["a"]["counts"]
    return {
        "schema": REPORT_SCHEMA,
        "kind": kind,
        "label": label,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "content_fingerprint": pair["content_fingerprint"],
        "scorer_sha256": hashlib.sha256(Path(score.__file__).read_bytes()).hexdigest(),
        "delivery": pair["delivery"],
        "runs": runs,
        "measures": pair["measures"],
        "fields": _by_field(scores),
        "missed_high_critical": pair["missed_high_critical"],
        # n: the findings the list is drawn from; the smaller of the two runs' scored sets.
        "missed_high_critical_n": min(
            scores[side]["counts"]["review"]["high_critical_findings"] for side in "ab"
        ),
        "missed_high_critical_explanation": MISSED_HIGH_CRITICAL_EXPLANATION,
        "counts": {
            "review_docs": c["review"]["docs"],
            "extraction_items": c["extraction"]["items"],
            "qa_questions": c["qa"]["questions"],
            "past_25k": {
                "review_findings": c["review"]["findings_by_bucket"].get("past_25k", 0),
                "extraction_facts": c["extraction"]["units_by_bucket"].get("past_25k", 0),
                "qa_questions": c["qa"]["past_25k"],
            },
            "routing_workflows_covered": c["routing"]["workflows_covered"],
            "routing_requests_per_workflow": c["routing"]["requests_per_workflow"],
            "failed_calls": sum(r["failed_calls"] for r in runs),
            "owner_prefill_changed_rate": c["owner"]["prefill_changed_rate"],
            "owner_rows_unmarked": c["owner"]["rows_unmarked"],
            "extraction_not_stated_passages": sum(
                c["extraction"].get("not_stated_passages", {}).values()
            ),
        },
        "history": superseded_history(),
        "comparison": comparison,
    }


def _measure_table(measures: list[dict], role: str) -> list[str]:
    rows = []
    for m in measures:
        if m["role"] != role:
            continue
        key, v = m["key"], m["values"]
        rows.append(
            [
                m["label"],
                m["n"],
                _fmt(key, v["run_a"]),
                _fmt(key, v["run_b"]),
                _fmt(key, v["average"]),
                _band(key, v["noise_band"]),
                _explained(m),
            ]
        )
        for name, split in m["splits"].items():
            rows.append(
                [
                    f"· {name.replace('_', ' ')}",
                    split["n"],
                    _fmt(key, split.get("run_a")),
                    _fmt(key, split.get("run_b")),
                    _fmt(key, split.get("average")),
                    _band(key, split.get("noise_band")),
                    "",
                ]
            )
    return _table("Measure|n|Run A|Run B|Average|Noise band|What it means", rows)


def _runs_table(runs: list[dict]) -> list[str]:
    def rate(r):
        return (
            f"{r['failed_calls']} ({r['failed_call_rate']:.1%})"
            if r["failed_call_rate"] is not None
            else "—"
        )

    rows = [
        ("Run id", lambda r: _short(r["run_id"])),
        ("Mode", lambda r: r["mode"]),
        ("Delivery", lambda r: r["delivery"]),
        ("Outcome", lambda r: r["outcome"]),
        ("Flags", lambda r: ", ".join(r["flags"]) or "none"),
        ("Failed AI calls", rate),
        ("Items not scored", lambda r: len(r["failed"]["items"])),
        ("Cost, actual", lambda r: _fmt("cost_usd", r["cost_usd"])),
        ("Cost, estimate", lambda r: _fmt("cost_usd", r["estimate_usd"])),
        ("Run time", lambda r: _fmt("wall_time_s", r["wall_time_s"])),
        ("Model under test", lambda r: r["versions"]["model_under_test"]),
        ("Models used", lambda r: ", ".join(r["versions"]["models"] or [])),
        ("App code version", lambda r: _short(r["versions"]["app_code_sha256"], 12)),
        ("Prompt versions", lambda r: _short(r["versions"]["prompts_sha256"], 12)),
    ]
    if any(r.get("change") for r in runs):
        rows.insert(
            2,
            ("Change / target", lambda r: f"{r['change'] or '—'} / {r['target_area'] or '—'}"),
        )
    table = [[name, *(read(r) for r in runs)] for name, read in rows]
    return _table("|Run A|Run B", table)


def _cost_table(runs: list[dict]) -> list[str]:
    types = list(dict.fromkeys(t for r in runs for t in r["cost_by_call_type"]))
    rows = []
    for t in types:
        row = [t]
        for r in runs:
            entry = r["cost_by_call_type"].get(t) or {}
            row += [
                _fmt("cost_usd", entry.get("estimate_usd")),
                _fmt("cost_usd", entry.get("actual_usd")),
            ]
        rows.append(row)
    return _table("Call type|A estimate|A actual|B estimate|B actual", rows)


def _verdict_lines(verdict: dict, labels: dict[str, dict]) -> list[str]:
    word = {"pass": "PASS", "fail": "FAIL", "no_verdict": "NO VERDICT"}[verdict["verdict"]]
    lines = [f"## Verdict: {word}", ""]
    main = labels.get(verdict["main_measure"], {}).get("label", "—")
    lines.append(
        f"Baseline: {verdict.get('baseline') or '—'} · "
        f"change: {verdict.get('change') or '—'} · "
        f"target area: {verdict.get('target_area') or 'not recorded'} · main measure: {main} · "
        f"delivery: {verdict['delivery']}"
    )
    lines.append("")
    if verdict["no_verdict_reason"]:
        lines += [f"No verdict was given: {verdict['no_verdict_reason']}.", ""]
    if verdict["failed_measures"]:
        names = ", ".join(labels[k]["label"] for k in verdict["failed_measures"])
        lines += [f"Measures that failed: **{names}**.", ""]
    shown = {"improved": "improved", "worsened": "worsened", "within_band": "within the noise band"}
    rows = []
    for m in verdict["per_measure"]:
        key = m["key"]
        mark = " (main measure)" if key == verdict["main_measure"] else ""
        fail = "  ← FAILED" if m.get("passed") is False else ""
        rows.append(
            [
                labels[key]["label"] + mark,
                _fmt(key, m["baseline_average"]),
                _fmt(key, m["change_average"]),
                _fmt(key, m["difference"]),
                _band(key, m["noise_band"]),
                shown.get(m["result"], "not measured") + fail,
            ]
        )
    lines += ["Scores that decide pass or fail:", ""]
    lines += _table("Measure|Baseline|Change|Difference|Noise band|Result", rows)
    rows = [
        [
            labels[m["key"]]["label"],
            _fmt(m["key"], m["baseline_average"]),
            _fmt(m["key"], m["change_average"]),
            _fmt(m["key"], m["difference"]),
            _band(m["key"], m["noise_band"]),
        ]
        for m in verdict["report_only"]
    ]
    if rows:
        lines += ["Also reported, never decide pass or fail:", ""]
        lines += _table("Measure|Baseline|Change|Difference|Noise band", rows)
    rows = [
        [
            labels[m["key"]]["label"],
            _fmt(m["key"], m["baseline"]),
            _fmt(m["key"], m["change"]),
            _fmt(m["key"], m["difference"]),
        ]
        for m in verdict["cost_time"]
    ]
    if rows:
        lines += [
            "Cost and time, beside the scores (the owner weighs them; they never decide):",
            "",
        ]
        lines += _table("Measure|Baseline|Change|Difference", rows)
    return lines


def render_markdown(obj: dict) -> str:
    """The readable form of a committed report: every measure with its n, a plain-language line and
    its noise band; cost and time beside the scores; the flags and the sample label up top."""
    runs, measures = obj["runs"], obj["measures"]
    title = "Baseline report" if obj["kind"] == "baseline" else "Change comparison"
    lines = [f"# {title}: {obj['label']}", ""]
    lines += _banner(runs)
    lines += [
        f"Delivery mode: {_DELIVERY.get(obj['delivery'], obj['delivery'])}",
        (
            f"Exam content: `{_short(obj['content_fingerprint'], 12)}` · scorer: "
            f"`{_short(obj['scorer_sha256'], 12)}` · generated {obj['generated_at']}"
        ),
        "",
        (
            "Numbers, counts and names only: no contract text. Each score shows how many items it "
            "rests on (n); a small n is indicative, not conclusive. The noise band is how far the "
            "two runs differ from each other: a change smaller than that is not a real difference."
        ),
        "",
    ]
    labels = {m["key"]: m for m in measures}
    if obj["comparison"]:
        lines += _verdict_lines(obj["comparison"], labels)
        lines += ["## The change's two runs", ""]
    else:
        lines += ["## The two runs", ""]
    lines += _runs_table(runs)
    sections = (
        (HEADLINE, "Scores that decide pass or fail"),
        (REPORT_ONLY, "Also reported (never decide pass or fail)"),
        (COST_TIME, "Cost and time (reported beside the scores, never decide)"),
    )
    for role, title in sections:
        table = _measure_table(measures, role)
        if table:
            lines += [f"## {title}", "", *table]
    rows = [
        [
            name,
            f["all"]["n"],
            _fmt("", f["all"]["run_a"]),
            _fmt("", f["all"]["run_b"]),
            _fmt("", f["all"]["average"]),
            _band("", f["all"]["noise_band"]),
            _fmt("", f["before_25k"]["average"]),
            f["before_25k"]["n"],
            _fmt("", f["past_25k"]["average"]),
            f["past_25k"]["n"],
        ]
        for name, f in obj["fields"].items()
    ]
    if rows:
        lines += [
            "## Extraction accuracy, per field",
            "",
            (
                "Share of each fact extracted correctly, with the average split before and past "
                "the first 25,000 characters of the document."
            ),
            "",
            *_table("Field|n|Run A|Run B|Average|Noise band|Before 25k|n|Past 25k|n", rows),
        ]
    cost = _cost_table(runs)
    if cost:
        lines += ["## Actual cost against the estimate, per call type", "", *cost]
    missed = obj["missed_high_critical"]
    lines += [
        "## Missed high and critical findings",
        "",
        f"{obj['missed_high_critical_explanation']} (n = {obj['missed_high_critical_n']})",
        "",
    ]
    if missed:
        rows = [
            [m["item_id"], m["rule"], m["severity"], " and ".join(m["runs_missed"]).upper()]
            for m in missed
        ]
        lines += _table("Item|Rule|Severity|Missed in run", rows)
    else:
        lines += ["None.", ""]
    c = obj["counts"]
    lines += [
        "## Counts",
        "",
        f"- Review documents by source: {_listed(c['review_docs'])}",
        f"- Extraction documents by source: {_listed(c['extraction_items'])}",
        f"- Question-answering questions by source: {_listed(c['qa_questions'])}",
        f"- Past the first 25,000 characters: {_listed(c['past_25k'])}",
        f"- Routing: {c['routing_workflows_covered']} workflows covered by at least one request",
        f"- Failed AI calls, both runs: {c['failed_calls']}",
        f"- Owner prefilled answers the owner changed: {_fmt('', c['owner_prefill_changed_rate'])}",
        f"- Owner rows left unmarked, not scored: {c['owner_rows_unmarked']}",
        (
            "- Clause facts a document does not state, not scored (the app cannot answer "
            f"\"none\" for a clause): {c.get('extraction_not_stated_passages', 0)}"
        ),
        "",
    ]
    rows = [
        [w["name"] or wid, w["requests"]] for wid, w in c["routing_requests_per_workflow"].items()
    ]
    if rows:
        lines += ["Routing requests per workflow:", "", *_table("Workflow|Requests", rows)]
    failed = [
        (f"Run {n}", r) for n, r in zip("AB", runs) if r["failed"]["calls"] or r["failed"]["items"]
    ]
    if failed:
        lines += ["## Failed calls and items not scored", ""]
        for name, r in failed:
            rows = [
                [x["call_id"], x["call_type"], x["status"], x["doc_id"]]
                for x in r["failed"]["calls"]
            ]
            lines += [
                f"{name} failed AI calls:",
                "",
                *_table("Call|Type|Status|Document", rows),
            ]
            rows = [
                [x["item_id"], x["task"], x["status"], x["reason_code"]]
                for x in r["failed"]["items"]
            ]
            lines += [
                f"{name} items not scored:",
                "",
                *_table("Item|Task|Status|Reason", rows),
            ]
    history = obj["history"]
    if history:
        lines += [
            f"## Earlier results ({SUPERSEDED_LABEL})",
            "",
            "Kept as history only. They are not a baseline and nothing here is compared with them.",
            "",
            *[f"- `{h['file']}` — {h['label']}" for h in history],
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _private_keys(value, found: set[str]) -> set[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _PRIVATE_KEYS:
                found.add(key)
            _private_keys(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _private_keys(item, found)
    return found


def _owner_leak(text: str) -> str | None:
    """What in ``text`` came from an owner contract, or None: an owner file name, or
    ``SHINGLE_WORDS`` consecutive words of an owner contract's frozen text."""
    lowered = text.lower()
    for name in OWNER_FILES:
        if name.lower() in lowered:
            return "an owner file name"
    words = _WORDS.findall(lowered)
    seen = set(zip(*(words[i:] for i in range(SHINGLE_WORDS))))
    if not seen:
        return None
    for path in sorted(OWNER_TEXTS_DIR.glob("*.txt")) if OWNER_TEXTS_DIR.is_dir() else []:
        owner = _WORDS.findall(path.read_text(encoding="utf-8", errors="replace").lower())
        if not seen.isdisjoint(zip(*(owner[i:] for i in range(SHINGLE_WORDS)))):
            return f"{SHINGLE_WORDS} consecutive words of owner contract text ({path.stem[:8]})"
    return None


def write_public_report(obj: dict, *, stem: str) -> Path:
    """Write ``reports/<stem>.md`` and ``reports/<stem>.json``; returns the ``.md`` path.

    Refused (``ReportRefused``, nothing written) when the object holds a private key, an owner
    file name or eight words of an owner contract (FR-36), or when either file already exists:
    a published report is a record (FR-27), so replacing a baseline is a new name, never an
    overwrite."""
    if not _STEM.fullmatch(stem):
        raise ReportRefused(f"'{stem}' is not a usable report name")
    if keys := _private_keys(obj, set()):
        raise ReportRefused(f"the report carries private fields ({', '.join(sorted(keys))})")
    markdown = render_markdown(obj)
    body = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    if leak := _owner_leak(markdown + body):
        raise ReportRefused(f"the report contains {leak}; nothing was written")
    md_path, json_path = REPORTS_DIR / f"{stem}.md", REPORTS_DIR / f"{stem}.json"
    if md_path.exists() or json_path.exists():
        raise ReportRefused(f"report '{stem}' already exists; earlier reports are kept")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(body, encoding="utf-8")
    return md_path


# --- the private detail file ------------------------------------------------------------


def _planted_text(content: ExamContent | None, item_id: str, rule: str) -> str | None:
    """The key's own text for a missed finding: what was planted, or the evidence sentence."""
    for review in content.review if content else []:
        for key in review.rules if review.item_id == item_id else []:
            if key.rule == rule:
                planted = key.planted
                return (planted and (planted.changed_text or planted.original_text)) or (
                    key.evidence_quote
                )
    return None


def _flag_quote(item: Mapping | None, rule: str | None) -> str | None:
    """What the AI quoted for a false alarm: the flag for that rule, or the unattributed one."""
    for f in ((item or {}).get("prediction") or {}).get("flags") or []:
        loose = f.get("rule") is None or f.get("attributed_by") == "none"
        if loose if rule is None else f.get("rule") == rule:
            return f.get("quote")
    return None


def write_detail(
    results: dict, scores: dict, path: Path, *, content: ExamContent | None = None
) -> None:
    """The owner's private ``detail.md`` for one run: every measure, every failed call and item,
    invalid quotes, missed and false findings, per-call coverage. It quotes text, so it is only
    ever written under the git-ignored private runs folder (FR-36): any other place, committed
    or not, is refused. ``content`` (optional) adds the key's own text next to each missed
    finding."""
    path = Path(path)
    if RUNS_DIR.resolve() not in path.resolve().parents:
        raise ReportRefused("detail.md quotes contract text; it belongs in eval/private/runs")
    run, versions, counts = scores["run"], results.get("versions") or {}, scores["counts"]
    items = {i["item_id"]: i for i in results.get("items") or []}
    calls = {c.get("call_id"): c for c in results.get("calls") or []}
    reasons, cost, time = (
        scores["private"]["failure_reasons"],
        scores["cost_vs_estimate"],
        scores["time"],
    )
    routing = counts["routing"]
    why = f" ({results['incomplete_reason']})" if results.get("incomplete_reason") else ""
    models = ", ".join(versions.get("models") or [])
    code = _short(versions.get("app_code_sha256"), 12)
    fingerprint = _short(versions.get("content_fingerprint"), 12)
    rate = run["failed_call_rate"] or 0
    prompts = ", ".join(
        f"{p.get('prompt_key')} {p.get('version')}" for p in versions.get("prompts") or []
    )
    lines = [
        f"# Exam run {run['run_id']} — detail (private)",
        "",
        "Quotes contract text. Never commit, copy into a public report, or send anywhere.",
        "",
        *_banner([run], ("This run",)),
        f"- Mode: {run['mode']} · delivery: {run['delivery']} · outcome: {run['outcome']}{why}",
        f"- Started {results.get('started_at')} · finished {results.get('finished_at')}",
        f"- Model under test: {versions.get('model_under_test')} · models seen: {models}",
        f"- App code `{code}` · exam content `{fingerprint}`",
        f"- Prompts: {prompts}",
        f"- Failed AI calls: {counts['failed_calls']} of {counts['calls']} ({rate:.1%})",
        "",
    ]
    if not run["content_current"]:
        lines += [
            "**These answers were given to other exam content than the content now on disk;** "
            + "figures that use the keys may be wrong.",
            "",
        ]
    lines += [
        "## Time",
        "",
        f"- Wall time: {_fmt('wall_time_s', time['wall_time_s'])}",
        *(f"- Phase {p}: {_fmt('wall_time_s', t)}" for p, t in time["phase_wall_time_s"].items()),
        *(
            f"- Batch turnaround, phase {p}: {_fmt('wall_time_s', t)}"
            for p, t in time["batch_turnaround_s"].items()
        ),
        "",
        "## Routing",
        "",
        f"- Requests: {routing['requests']} · scored: {routing['scored']} · "
        + f"not picked by the owner: {routing['unpicked']} · "
        + f"out of date: {routing['out_of_date']}",
        f"- Workflows covered: {routing['workflows_covered']} of {routing['workflows_total']}",
        "",
    ]

    def usd(value):
        return _fmt("cost_usd", value)

    measure_rows = []
    for key, m in scores["measures"].items():
        measure_rows.append(
            [m["label"], m["role"].replace("_", " "), m["n"], _fmt(key, m["value"]), _explained(m)]
        )
        measure_rows += [
            [f"· {name.replace('_', ' ')}", "", sp["n"], _fmt(key, sp["value"]), ""]
            for name, sp in m["splits"].items()
        ]
    failed_calls = [
        [
            f["call_id"],
            f["call_type"],
            f["status"],
            f["doc_id"],
            ", ".join(f["item_ids"]),
            (calls.get(f["call_id"]) or {}).get("model"),
            (calls.get(f["call_id"]) or {}).get("stop_reason"),
        ]
        for f in scores["failed_calls"]
    ]
    batch_keys = ("requests", "admitted", "succeeded", "errored", "expired", "canceled")
    # (title, headers, rows, always): a section with nothing to show says "None." only when the
    # owner needs to see that nothing went wrong; the rest are left out when empty.
    sections = [
        ("Every measure", "Measure|Role|n|Value|What it means", measure_rows, True),
        (
            "Cost against the estimate",
            "Call type|Estimate|Actual",
            [["total", usd(cost["total"]["estimate_usd"]), usd(cost["total"]["actual_usd"])]]
            + [
                [t, usd(v["estimate_usd"]), usd(v["actual_usd"])]
                for t, v in cost["by_call_type"].items()
            ],
            True,
        ),
        (
            "Calls by type",
            "Type|Calls|Batched|Failed|Cut off|Tokens|Cost|List cost|Coverage|Latency",
            [
                [
                    t,
                    v["calls"],
                    v["batched"],
                    v["failed"],
                    v["truncated"],
                    f"{v['total_tokens']:,}",
                    usd(v["cost_usd"]),
                    usd(v["list_cost_usd"]),
                    _fmt("", v["coverage_avg"]),
                    _fmt("latency_ms_avg", v["latency_ms_avg"]),
                ]
                for t, v in scores["by_call_type"].items()
            ],
            True,
        ),
        (
            "Failed AI calls (FR-40)",
            "Call|Type|Status|Document|Items|Model|Stop reason",
            failed_calls,
            True,
        ),
        (
            "Items not scored",
            "Item|Task|Document|Status|Reason",
            [
                [
                    i["item_id"],
                    i["task"],
                    i["doc_id"],
                    i["status"],
                    reasons.get(i["item_id"]) or i["reason_code"],
                ]
                for i in scores["failed_items"]
            ],
            True,
        ),
        (
            "Documents that could not be read",
            "Document|Status|Reason",
            [
                [d["doc_id"], d["status"], d.get("failure")]
                for d in results.get("documents") or []
                if d.get("status") != "ok"
            ],
            False,
        ),
        (
            "Batches",
            "Phase|Batch|Retry|" + "|".join(batch_keys) + "|Submitted|Ended",
            [
                [
                    b.get("phase"),
                    _short(b.get("batch_id"), 20),
                    b.get("retry_of") and "retry",
                    *(b.get(k) for k in batch_keys),
                    b.get("submitted_at"),
                    b.get("ended_at"),
                ]
                for b in results.get("batches") or []
            ],
            False,
        ),
        (
            "Quotes not found word for word in the source",
            "Call|Type|Document|Quote",
            [
                [q["call_id"], q["call_type"], q["doc_id"], q["text"]]
                for q in scores["private"]["invalid_quotes"]
            ],
            True,
        ),
        (
            "Missed high and critical findings",
            "Item|Rule|Severity|What the key holds",
            [
                [
                    m["item_id"],
                    m["rule"],
                    m["severity"],
                    _planted_text(content, m["item_id"], m["rule"]),
                ]
                for m in scores["missed_high_critical"]
            ],
            True,
        ),
        (
            "False alarms",
            "Item|Rule|What the AI quoted",
            [
                [a["item_id"], a["rule"], _flag_quote(items.get(a["item_id"]), a["rule"])]
                for a in scores["false_alarms"]
            ],
            True,
        ),
        (
            "Extraction, per field",
            "Field|n|Accuracy|Before 25k|n|Past 25k|n",
            [
                [
                    f,
                    v["n"],
                    _fmt("", v["accuracy"]),
                    _fmt("", v["before_25k"]["value"]),
                    v["before_25k"]["n"],
                    _fmt("", v["past_25k"]["value"]),
                    v["past_25k"]["n"],
                ]
                for f, v in scores["fields"].items()
            ],
            False,
        ),
        (
            "Portfolio questions",
            "Question|Expected|Found|Missed|Wrongly included|Missed documents|Wrong documents",
            [
                [
                    q["item_id"],
                    q["expected"],
                    q["found"],
                    q["missed"],
                    q["wrongly_included"],
                    ", ".join(q["missed_doc_ids"]),
                    ", ".join(q["wrong_doc_ids"]),
                ]
                for q in scores["portfolio"]["questions"]
            ],
            False,
        ),
        (
            "Document coverage, per call",
            "Call|Type|Document|Seen|Total|Coverage",
            [
                [
                    c["call_id"],
                    c["call_type"],
                    c["doc_id"],
                    c["chars_seen"],
                    c["chars_total"],
                    _fmt("", c["coverage"]),
                ]
                for c in scores["coverage_per_call"]
            ],
            False,
        ),
    ]
    for title, headers, rows, always in sections:
        if rows or always:
            lines += [f"## {title}", "", *(_table(headers, rows) or ["None.", ""])]
    left_out = {k: v for k, v in (results.get("excluded") or {}).items() if v}
    not_stated = scores["counts"]["extraction"].get("not_stated_passages") or {}
    if not_stated:
        left_out["clause facts not stated (the app cannot answer none)"] = _listed(not_stated)
    if left_out:
        lines += [
            "## Left out of scoring",
            "",
            *(f"- {k.replace('_', ' ')}: {v}" for k, v in left_out.items()),
            "",
        ]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
