"""``python -m eval.exam <command>``: the AI exam's command line (specs/005-ai-eval-baseline T019).

Run it from ``backend/`` (``/app`` in the container), in a terminal:

    docker compose exec backend python -m eval.exam <command> ...

The exam is manual and owner-only (FR-31): nothing in the app, CI or a schedule starts it. Every
command that can touch a client contract, spend money or change what counts as the baseline asks
for the owner's email and AEGIS password again, and every paid one shows its estimate and asks for
a go-ahead and a hard spending ceiling before anything is sent. Exit codes: 0 done, 2 refused (the
reason is printed and nothing was spent), 1 an error.

Order matters in this file. ``env.isolate()`` must run before anything imports the app's database
module (it builds the default engine from ``settings.database_url`` at import time, and ``isolate``
is what moves that onto the throwaway exam database -- FR-37, FR-38). So the top of this file
imports only the standard library, SQLAlchemy, ``eval.exam.env`` and ``eval.exam.content`` (none of
which reaches the database), and every other exam module is imported inside a command, after
``isolate`` returned. A test starts a fresh interpreter to check that stays true.
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from eval.exam import env
from eval.exam.content import REPORTS_DIR, ExamContent, Task
from eval.exam.env import ExamRefused, LiveDB, Owner

# What a baseline or a change may be called: it becomes part of a report's file name, so it is
# checked before anything is spent on a run that would carry it.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

# command -> (only the owner may run it, it makes Claude calls with the exam's own key).
# `ocr` pays the OCR provider, not Anthropic, so it needs no exam key; `build`, `status` and
# `compare` read and write only numbers and public content, so nobody is asked for a password.
_COMMANDS = {
    "build": (False, False),
    "status": (False, False),
    "compare": (False, False),
    "owner-import": (True, False),
    "ocr": (True, False),
    "prefill": (True, True),
    "load-facts": (True, False),
    "sheet": (True, False),
    "draft-routing": (True, True),
    "load-routing": (True, False),
    "run": (True, True),
    "baseline": (True, False),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.exam",
        description="The AI exam: measure how well the AI does today, so a change can be judged "
        "against it. Commands marked [paid] spend money after you approve an estimate.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add(name: str, summary: str, *, owner: bool = True) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=summary, description=summary)
        if owner:
            sub.add_argument(
                "--owner", metavar="EMAIL", help="the exam owner (default: EVAL_OWNER_EMAIL)"
            )
        return sub

    sub = add("build", "Build the exam's questions and documents (free).", owner=False)
    sub.add_argument(
        "--only",
        choices=("public", "constructed"),
        help="build just one half (default: both, with one report)",
    )
    add(
        "status",
        "Show where the exam stands: content, your files, runs, baseline (free).",
        owner=False,
    )
    sub = add("owner-import", "Copy your 14 contract files into the exam's private folder (free).")
    sub.add_argument(
        "--from", dest="src_dir", required=True, metavar="DIR", help="the folder that holds them"
    )
    add("ocr", "[paid] Read your scanned contracts with the OCR provider, once.")
    add("prefill", "[paid] Have the AI pre-fill the facts sheet for you to check.")
    sub = add("load-facts", "Load your checked facts sheet as the answer key (free).")
    sub.add_argument("path", help="the .xlsx file in backend/eval/private/sheets")
    sub = add("sheet", "Write a fresh facts sheet from the answers you already gave (free).")
    sub.add_argument("kind", choices=("facts",))
    sub.add_argument(
        "--reconfirm",
        action="store_true",
        help="only the answers whose contract file or text has changed since you confirmed them",
    )
    add("draft-routing", "[paid] Have the AI draft sample requests and a sheet for you to route.")
    sub = add("load-routing", "Load your checked routing sheet as the answer key (free).")
    sub.add_argument("path", help="the .xlsx file in backend/eval/private/sheets")
    sub = add("run", "[paid] Run the exam, or resume or abandon a run that is waiting.")
    which = sub.add_mutually_exclusive_group(required=True)
    which.add_argument("--mode", choices=("sample", "full"), help="sample is a cheap rehearsal")
    which.add_argument("--resume", metavar="RUN_ID", help="carry on with a run waiting on a batch")
    which.add_argument("--abandon", metavar="RUN_ID", help="cancel a waiting run and close it")
    sub.add_argument(
        "--delivery",
        choices=("batch", "sync"),
        default="batch",
        help="batch (default): half price, answers arrive later; sync: every call sent normally",
    )
    sub.add_argument("--change", metavar="NAME", help="what you changed (with --target)")
    sub.add_argument(
        "--target",
        choices=[t.value for t in Task],
        help="the area the change is meant to improve (with --change)",
    )
    sub = commands.add_parser("baseline", help="Designate two full runs as the baseline (free).")
    actions = sub.add_subparsers(dest="action", required=True)
    sub = actions.add_parser("set", help="make two full runs the baseline")
    sub.add_argument("name")
    sub.add_argument("run_a")
    sub.add_argument("run_b")
    sub.add_argument("--owner", metavar="EMAIL", help="the exam owner (default: EVAL_OWNER_EMAIL)")
    sub.add_argument(
        "--replace",
        action="store_true",
        help="name this pair as the baseline in place of the current one; the earlier report is "
        "kept, never overwritten",
    )
    sub = add("compare", "Judge a change's two runs against the baseline (free).", owner=False)
    sub.add_argument("run_a")
    sub.add_argument("run_b")
    sub.add_argument("--baseline", metavar="NAME", help="default: the current baseline")
    return parser


# --- reading what the audit trail says about runs (the immutable run records, FR-39) -----


def _audit_rows(live: LiveDB, action: str) -> list:
    """(resource_id, after, created_at) of every ``action`` audit row, oldest first."""
    from app.core.models import AuditLog

    with live.sessions() as db:
        return list(
            db.execute(
                select(AuditLog.resource_id, AuditLog.after, AuditLog.created_at)
                .where(AuditLog.org_id == live.org_id, AuditLog.action == action)
                .order_by(AuditLog.created_at, AuditLog.id)
            ).all()
        )


def _run_id(live: LiveDB, text: str) -> str:
    """A run id as typed: all of it, or its start (at least 8 characters, as the reports show it)."""
    ids = [row.resource_id for row in _audit_rows(live, "eval.spend_approved")]
    found = [i for i in ids if i == text] or [
        i for i in ids if len(text) >= 8 and i.startswith(text)
    ]
    if not found:
        raise ExamRefused(f"no exam run matches '{text}'; `status` lists the runs")
    if len(found) > 1:
        raise ExamRefused(f"'{text}' matches {len(found)} runs; type more of the id")
    return found[0]


def _finished(live: LiveDB, run_id: str) -> dict | None:
    """The run's ``eval.run_finished`` record with its id added, the shape ``check_pair`` reads."""
    for row in _audit_rows(live, "eval.run_finished"):
        if row.resource_id == run_id:
            return {**row.after, "run_id": row.resource_id}
    return None


def _baselines(live: LiveDB) -> list[dict]:
    """Every baseline ever designated, oldest first; the last one is the current baseline."""
    return [row.after for row in _audit_rows(live, "eval.baseline_designated")]


def _pair(content: ExamContent, records: list[dict]) -> dict:
    """Two finished runs scored side by side, with the current scorer (re-scored every time)."""
    from eval.exam import report, score

    a, b = (
        json.loads((report.RUNS_DIR / r["run_id"] / "results.json").read_text(encoding="utf-8"))
        for r in records
    )
    return score.score_pair(a, b, content)


def _refuse_unfit(records: list[dict | None], what: str) -> None:
    """Refuse unless the two finished runs are fit to be a pair (``report.check_pair``)."""
    from eval.exam import report

    problems = report.check_pair(*records)
    if problems:
        raise ExamRefused(f"{what}:\n  - " + "\n  - ".join(problems))


# --- commands --------------------------------------------------------------------------------


def _build(args: argparse.Namespace, live: LiveDB, owner: Owner | None) -> None:
    from eval.exam import build

    if args.only == "public":
        build.run_public()
    else:
        build.run_constructed(with_public=args.only is None)


def _status(args: argparse.Namespace, live: LiveDB, owner: Owner | None) -> None:
    from eval.exam import owner as owner_mod
    from eval.exam import report, routing, runner
    from eval.exam.content import content_fingerprint, load_content

    try:
        content = load_content()
    except ValueError as exc:
        print(f"Exam content: cannot be loaded ({exc})")
    else:
        datasets = {d.key: d.available for d in content.datasets}
        missing = sorted(k for k, ok in datasets.items() if not ok)
        print(f"Exam content: {content_fingerprint(content)[:12]}")
        if not datasets:
            print("  Not built yet: run `build`.")
        else:
            print(
                "  Datasets: "
                + (f"MISSING {', '.join(missing)}" if missing else f"all {len(datasets)} present")
            )
        print(
            f"  {len(content.documents)} documents; review {len(content.review)}, extraction "
            f"{len(content.extraction)}, questions {len(content.qa)}, portfolio "
            f"{len(content.portfolio)}, routing requests {len(content.routing)}"
        )
        picked = sum(k.status != "unmarked" for k in content.routing_keys)
        if content.workflow_snapshot is None:
            print("  Routing: no requests drafted yet (`draft-routing`)")
        else:
            state = (
                "current"
                if routing.routing_fresh(live, content.workflow_snapshot)
                else "OUT OF DATE (the workflow list changed; routing is left out of runs)"
            )
            print(f"  Routing: {picked} of {len(content.routing)} picked; workflow list {state}")

    s = owner_mod.owner_status()
    print(
        f"Your contracts: {s['files']} of {s['expected_files']} imported "
        f"({', '.join(f'{n} {k}' for k, n in s['kinds'].items()) or 'none'}); scans read "
        f"{s['ocr_cached']} of {s['ocr_needed']}; text frozen {s['frozen_texts']} of {s['files']}"
    )
    keys = s["keys"]
    print(
        f"Your answers: {keys['confirmed']} confirmed, {keys['corrected']} corrected, "
        f"{keys['unmarked']} unmarked; {keys['out_of_date']} out of date"
    )

    rows = _audit_rows(live, "eval.run_finished")
    print(f"Runs ({len(rows)} finished" + (", newest 10 shown):" if len(rows) > 10 else "):"))
    for row in reversed(rows[-10:]):
        a = row.after
        line = [row.resource_id, a.get("kind")]
        line += [a["mode"]] if a.get("mode") else []
        line += [
            a.get("delivery"),
            a.get("outcome"),
            f"${(a.get('actual') or {}).get('usd') or 0:,.2f}",
        ]
        if a.get("failed_call_rate"):
            line.append(f"failed calls {a['failed_call_rate']:.1%}")
        if flags := report.run_flags(a):
            line.append(f"[{', '.join(flags)}]")
        print("  " + "  ".join(str(part) for part in line))
    for row in runner._unfinished_runs(live):
        waiting = runner._waiting_on_batch(row.resource_id)
        print(
            f"  {row.resource_id}  {row.after['kind']}  "
            + (
                "WAITING ON A BATCH: `run --resume` it, or `run --abandon` it"
                if waiting
                else "started but not finished (recorded as incomplete the next time a run starts)"
            )
        )
    baselines = _baselines(live)
    if baselines:
        b = baselines[-1]
        print(f"Baseline: {b['name']} (runs {b['runs'][0][:8]} and {b['runs'][1][:8]})")
    else:
        print("Baseline: none yet (`baseline set NAME RUN_A RUN_B`)")


def _owner_import(args: argparse.Namespace, live: LiveDB, owner: Owner) -> None:
    from eval.exam import owner as owner_mod

    asyncio.run(owner_mod.owner_import(live, owner, src_dir=Path(args.src_dir)))


def _ocr(args: argparse.Namespace, live: LiveDB, owner: Owner) -> None:
    from eval.exam import owner as owner_mod

    asyncio.run(owner_mod.run_ocr(live=live, owner=owner, ask=input))


def _prefill(args: argparse.Namespace, live: LiveDB, owner: Owner) -> None:
    from eval.exam import owner as owner_mod

    asyncio.run(owner_mod.run_prefill(live=live, owner=owner, ask=input))


def _sheet_path(raw: str) -> Path:
    """A sheet as typed: a full path, or just its file name inside the private sheets folder."""
    from eval.exam import sheets

    path = Path(raw)
    return path if path.is_absolute() or path.exists() else sheets.SHEETS_DIR / path


def _load_facts(args: argparse.Namespace, live: LiveDB, owner: Owner) -> None:
    from eval.exam import owner as owner_mod

    owner_mod.load_facts(live, owner, _sheet_path(args.path))


def _sheet(args: argparse.Namespace, live: LiveDB, owner: Owner) -> None:
    from eval.exam import owner as owner_mod

    owner_mod.regenerate_facts_sheet(reconfirm=args.reconfirm)


def _draft_routing(args: argparse.Namespace, live: LiveDB, owner: Owner) -> None:
    from eval.exam import routing

    result = asyncio.run(routing.draft_routing(live=live, owner=owner, ask=input))
    if result is None:
        raise ExamRefused("the go-ahead was not given: nothing was spent")
    cover = result["coverage"]
    print(
        f"Drafted {result['requests']} requests covering {cover['workflows_covered']} of "
        f"{cover['workflows']} workflows (cost ${result['usd']:,.2f}).\n"
        f"Routing sheet: {result['sheet']}\n"
        "Open it, mark each request, save it, then run `load-routing`."
    )


def _load_routing(args: argparse.Namespace, live: LiveDB, owner: Owner) -> None:
    from eval.exam import routing

    counts = routing.load_routing(_sheet_path(args.path), live=live, owner=owner)
    print(
        f"Loaded {sum(counts.values())} requests: {counts['confirmed']} correct, "
        f"{counts['corrected']} corrected, {counts['unmarked']} left unmarked (not scored)."
    )


def _run(args: argparse.Namespace, live: LiveDB, owner: Owner) -> None:
    from eval.exam import runner

    if args.resume or args.abandon:
        run_id = _run_id(live, args.resume or args.abandon)
        if args.resume:
            asyncio.run(runner.resume_exam_run(live=live, owner=owner, run_id=run_id, ask=input))
        else:
            asyncio.run(runner.abandon_exam_run(live=live, owner=owner, run_id=run_id))
        return
    results = asyncio.run(
        runner.execute_exam_run(
            live=live,
            owner=owner,
            mode=args.mode,
            delivery=args.delivery,
            change=args.change,
            target=args.target,
            ask=input,
        )
    )
    print(f"Private detail: backend/eval/private/runs/{results['run_id']}/detail.md")
    if args.mode == "sample":
        print("This was a sample run: a cheap rehearsal that can never be part of a baseline.")


def _baseline_set(args: argparse.Namespace, live: LiveDB, owner: Owner) -> None:
    from eval.exam import report
    from eval.exam.content import load_content

    ids = [_run_id(live, args.run_a), _run_id(live, args.run_b)]
    records = [_finished(live, i) for i in ids]
    _refuse_unfit(records, "these two runs cannot be a baseline")
    baselines = _baselines(live)
    current = baselines[-1] if baselines else None
    if current and not args.replace:
        raise ExamRefused(
            f"baseline '{current['name']}' is already in place. To name this pair instead, add "
            "--replace; the earlier baseline's report stays in the reports folder."
        )
    obj = report.build_report(_pair(load_content(), records), kind="baseline", label=args.name)
    path = report.write_public_report(obj, stem=f"baseline-{args.name}")
    try:
        env.audit(
            live,
            action="eval.baseline_designated",
            actor=owner,
            resource_type="eval_baseline",
            resource_id=None,
            after={
                "name": args.name,
                "runs": ids,
                "delivery": records[0]["delivery"],
                "replaced": (
                    {"name": current["name"], "runs": current["runs"]} if current else None
                ),
                "report": path.name,
            },
        )
    except BaseException:
        # Report first, designation second (a refused report must not leave a baseline with
        # nothing behind it). If the designation then fails nothing was designated, so the
        # report may not stand: it would block this name and look like a published baseline.
        path.unlink(missing_ok=True)
        path.with_suffix(".json").unlink(missing_ok=True)
        raise
    print(f"Baseline '{args.name}' is in place. Report: {path}")


def _compare(args: argparse.Namespace, live: LiveDB, owner: Owner | None) -> None:
    from eval.exam import report, score
    from eval.exam.content import load_content

    baselines = _baselines(live)
    if not baselines:
        raise ExamRefused("there is no baseline yet: designate one with `baseline set`")
    if args.baseline:
        base = next((b for b in reversed(baselines) if b["name"] == args.baseline), None)
        if base is None:
            raise ExamRefused(f"no baseline is named '{args.baseline}'")
    else:
        base = baselines[-1]
    content = load_content()
    # The baseline's own runs are checked again: a results file changed since would otherwise
    # quietly move the line a change is judged against.
    base_runs = [_finished(live, i) for i in base["runs"]]
    _refuse_unfit(base_runs, f"baseline '{base['name']}' is no longer sound")
    ids = [_run_id(live, args.run_a), _run_id(live, args.run_b)]
    runs = [_finished(live, i) for i in ids]
    _refuse_unfit(runs, "these two runs cannot be compared")
    change = runs[0].get("change")
    if change != runs[1].get("change"):
        raise ExamRefused("the two runs were made for different changes")
    baseline_pair, change_pair = _pair(content, base_runs), _pair(content, runs)
    baseline_pair["label"], change_pair["label"] = base["name"], change
    verdict = score.verdict(baseline_pair, change_pair)
    obj = report.build_report(
        change_pair, kind="comparison", label=change or "unnamed", comparison=verdict
    )
    # A second comparison of the same change on the same day gets its own number, never an
    # overwrite: reports are records.
    stem = first = f"compare-{change or 'unnamed'}-{datetime.now(UTC):%Y%m%d}"
    number = 2
    while any((REPORTS_DIR / f"{stem}{ext}").exists() for ext in (".md", ".json")):
        stem, number = f"{first}-{number}", number + 1
    path = report.write_public_report(obj, stem=stem)
    print(
        f"Verdict: {verdict['verdict'].replace('_', ' ').upper()} (against baseline {base['name']})"
    )
    if verdict["no_verdict_reason"]:
        print(f"  No verdict was given: {verdict['no_verdict_reason']}.")
    if verdict["failed_measures"]:
        print(f"  Measures that got worse: {', '.join(verdict['failed_measures'])}")
    print(f"Report: {path}")


_HANDLERS = {
    "build": _build,
    "status": _status,
    "owner-import": _owner_import,
    "ocr": _ocr,
    "prefill": _prefill,
    "load-facts": _load_facts,
    "sheet": _sheet,
    "draft-routing": _draft_routing,
    "load-routing": _load_routing,
    "run": _run,
    "baseline": _baseline_set,
    "compare": _compare,
}


def _authenticate(live: LiveDB, args: argparse.Namespace) -> Owner:
    """The configured exam owner; a paid command asks for its spending ceiling at the terminal."""
    if not sys.stdin.isatty():
        raise ExamRefused(
            f"`{args.command}` asks for your spending ceiling, so it needs a terminal: "
            "run it as `docker compose exec backend python -m eval.exam ...` (without -T)"
        )
    return env.authenticate_owner(live, args.owner, command=args.command)


def _dispatch(args: argparse.Namespace) -> None:
    gated, paid = _COMMANDS[args.command]
    # First: the process is moved onto the throwaway database before any app module that builds
    # an engine is imported. Everything imported below is only safe because of this line.
    live = env.isolate(paid=paid)
    from eval.exam import report, score, sheets
    from eval.exam.content import OwnerFileRefused

    owner = _authenticate(live, args) if gated else None
    try:
        _HANDLERS[args.command](args, live, owner)
    except sheets.SheetRejected as exc:
        raise ExamRefused(
            "the sheet was rejected and nothing was loaded:\n  - " + "\n  - ".join(exc.problems)
        ) from exc
    except (score.ComparisonRefused, report.ReportRefused, OwnerFileRefused) as exc:
        raise ExamRefused(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    # Plain mistakes in the arguments are caught here, before the process is isolated, before the
    # owner types a password and before a run could be paid for: a change's name ends up in its
    # report's file name, and a change without its target (or the reverse) would be refused later.
    change, target = getattr(args, "change", None), getattr(args, "target", None)
    if (change or target) and not (getattr(args, "mode", None) and change and target):
        parser.error("--change and --target go together, on a new run (--mode)")
    for name in (change, getattr(args, "name", None)):
        if name and not _NAME.fullmatch(name):
            parser.error("a change or baseline name is letters, digits, '.', '_' or '-' (to 64)")
    try:
        _dispatch(args)
    except ExamRefused as exc:
        print(exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(
            "\nStopped. Anything already sent was recorded; a run that had been approved is "
            "closed as incomplete the next time a run starts, or can be picked up with "
            "`run --resume` if it was waiting on a batch.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
