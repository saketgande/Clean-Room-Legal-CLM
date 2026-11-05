"""How often AI calls fail, and how, from the AI call ledger (read-only). The standing "before and after" for
anything that touches model calls.

    docker compose exec backend python -m eval.ai_failure_report --days 30

Each failed ledger row names its outcome ("truncated: answer cut off ..."): app/ai/outcomes.py. Rows written
before that carry only the old wording or a class name, so they are classed from it and anything else shows as
`unclassified`. The ledger counts CALLS, not jobs: a retried job is several rows, so rates here are per call.
`legacy hidden failures` are metadata readings the regex fallback used to save as a successful job.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from datetime import timedelta

from app.ai.outcomes import Outcome

_NAMED = re.compile(r"^(\w+): ")
_TRANSIENT_CLASSES = ("RateLimit", "Overloaded", "InternalServer", "Timeout", "Connect", "ProviderUnavailable", "Transport")


def outcome_of(status: str, validation_error: str | None, error_class: str | None) -> str | None:
    """None for a call that succeeded, else the outcome it failed with."""
    if status == "succeeded":
        return None
    said = validation_error or ""
    named = _NAMED.match(said)
    if named and named.group(1) in Outcome.__members__.values():
        return named.group(1)
    if "cut off" in said:
        return Outcome.TRUNCATED.value
    if "tool output" in said or "validation" in said or "empty answer" in said:
        return Outcome.UNUSABLE.value
    if error_class and any(marker in error_class for marker in _TRANSIENT_CLASSES):
        return Outcome.TRANSIENT.value
    return "unclassified"


def summarize(rows: list[tuple[str, str, str | None, str | None]]) -> dict:
    """rows: (prompt_key, status, validation_error, error_class) -> per prompt key, calls and failures by outcome."""
    per: dict[str, dict] = defaultdict(lambda: {"calls": 0, "succeeded": 0, "failed": Counter()})
    for key, status, validation_error, error_class in rows:
        entry = per[key or "(no prompt key)"]
        entry["calls"] += 1
        outcome = outcome_of(status, validation_error, error_class)
        if outcome is None:
            entry["succeeded"] += 1
        else:
            entry["failed"][outcome] += 1
    return dict(per)


def render(summary: dict, jobs_by_outcome: dict[str, int], legacy_hidden: int, days: int) -> str:
    lines = [f"AI call failures, last {days} days (per call)", ""]
    lines.append(f"{'prompt':34} {'calls':>6} {'ok':>6}  failures")
    for key, entry in sorted(summary.items(), key=lambda kv: -sum(kv[1]["failed"].values())):
        failed = ", ".join(f"{name} {n}" for name, n in entry["failed"].most_common()) or "-"
        rate = f"{100 * entry['succeeded'] / entry['calls']:.0f}%" if entry["calls"] else "n/a"
        lines.append(f"{key[:34]:34} {entry['calls']:>6} {rate:>6}  {failed}")
    lines += ["", "Failed jobs by outcome (job_run.metadata_json.failure): "
              + (", ".join(f"{k} {v}" for k, v in sorted(jobs_by_outcome.items())) or "none recorded yet"),
              f"Legacy hidden failures (metadata reading saved from the regex fallback as a success): {legacy_hidden}"]
    return "\n".join(lines)


def main() -> None:
    from sqlalchemy import func, select

    from app.ai.models import AISkillRun
    from app.core.database import SessionLocal, utcnow
    from app.core.models import AICallLog
    from app.jobs.models import JobRun

    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    days = parser.parse_args().days
    since = utcnow() - timedelta(days=days)
    db = SessionLocal()
    try:
        rows = db.execute(
            select(AICallLog.prompt_key, AICallLog.status, AICallLog.validation_error, AICallLog.error_class)
            .where(AICallLog.created_at >= since, AICallLog.eval_run_id.is_(None))
        ).all()
        jobs = Counter()
        for (meta,) in db.execute(select(JobRun.metadata_json).where(JobRun.created_at >= since)).all():
            failure = (meta or {}).get("failure")
            if failure:
                jobs[failure.get("outcome", "unclassified")] += 1
        legacy = db.scalar(
            select(func.count()).select_from(AISkillRun).where(
                AISkillRun.created_at >= since, AISkillRun.validation_error.like("Claude failed; fallback used%"))
        ) or 0
        print(render(summarize([tuple(r) for r in rows]), dict(jobs), legacy, days))
    finally:
        db.close()


if __name__ == "__main__":
    main()
