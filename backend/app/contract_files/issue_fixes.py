"""Drafting a suggested change for a playbook issue, one issue at a time.

A playbook review lists issues. Some already come with a change, written from the
playbook rule's own wording (see playbooks.service._create_playbook_redline_version).
The rest, which have no rule wording to use, have no change, and a person would
click "Draft fix with AI" on each one. This module is the one place that does that,
for the three ways it is started:

  * the "Draft fix with AI" button on one issue (the ai-redline route),
  * the "Draft fixes for all" button, and
  * the job that runs after a playbook review, so the Review tab only has
    Accept / Reject cards left.

The last two are the same background job (``issue_fix_drafting``). It makes ONE AI
request per issue, one after another, and ties each result to its issue. One request
for all the issues together can't do that: its changes name no issue, so they land in
the Review tab as loose cards and every issue still reads "no change yet".

Each request is the assistant's edit engine (tool_runtime._edit_contract): it writes a
proposal version that is not authoritative and one ContractEdit per change, and it
never touches the contract until a person accepts. This module only chooses what to
ask for and ties the answer to the issue.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.contract_files import redline_edits
from app.contract_files.models import ContractEdit
from app.contracts.models import Contract
from app.core import org_access
from app.core.audit import write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.core.enums import JobStatus
from app.core.rbac import has_permission
from app.jobs.models import JobRun
from app.jobs.service import JobsService
from app.playbooks.models import PlaybookDeviation

logger = logging.getLogger(__name__)

JOB_TYPE = "issue_fix_drafting"
OPEN_ISSUE_STATUSES = ("open", "needs_review")

# A job gives up starting new issues after this long. Celery's soft limit is 720 s and
# its hard limit 900 s (jobs.celery_app); what was drafted by then is already saved.
JOB_BUDGET_SECONDS = 600
# Stop after this many AI requests in a row fail: the provider is down or the key is
# wrong, and every further request would fail the same way.
MAX_CONSECUTIVE_FAILURES = 3
# A queued or running job older than this is treated as dead (its worker is gone), so
# it neither hides a retry button nor blocks starting a new job. Jobs.celery's
# reclaim_stale_jobs settles the row itself, within its own lease.
STALE_AFTER = timedelta(minutes=25)
_MAX_INSTRUCTION_CHARS = 3900  # the ai-redline route accepts 4000


# ---- what to ask the AI ---------------------------------------------------------------


def fix_instruction(deviation: PlaybookDeviation) -> str:
    """The request for one issue. The Review tab builds the same sentence for its
    buttons (frontend _clm-workspace.tsx, devInstruction): change both together."""
    clause = (deviation.clause_type or "clause").replace("_", " ")
    quote = (deviation.citation or {}).get("quote") if isinstance(deviation.citation, dict) else None
    text = f"Fix the {clause} issue as a tracked change. Issue: {deviation.issue}"
    if deviation.suggested_fix:
        text += f" Required change: {deviation.suggested_fix}"
    if quote:
        text += f' The existing clause reads: "{quote}"'
    return text[:_MAX_INSTRUCTION_CHARS]


async def redline_with_ai(
    db: Session,
    *,
    contract: Contract,
    user: User,
    instructions: str,
    deviation: PlaybookDeviation | None = None,
) -> dict[str, Any]:
    """Ask the edit engine for tracked changes and, when they are for a playbook issue,
    tie every one of them to it (deciding them then closes the issue). Commits.

    Raises HTTPException 422 when the model can't find wording to change."""
    from app.ai.tool_registry import EditContractInput
    from app.ai.tool_runtime import tool_runtime

    result = await tool_runtime._edit_contract(
        db,
        payload=EditContractInput(contract_id=contract.id, instructions=instructions),
        user=user,
        session_id=f"clm-redline:{contract.id}",
    )
    if deviation is not None and result.get("status") == "created":
        # Every edit of this run is a fix for the issue; the issue closes once all are decided.
        rows = db.scalars(
            select(ContractEdit).where(
                ContractEdit.org_id == user.org_id,
                ContractEdit.contract_id == contract.id,
                ContractEdit.contract_version_id == result["base_version_id"],
            )
        ).all()
        for row in rows:
            if redline_edits.proposal_version_id(row.citation) == result["contract_version_id"]:
                row.citation = [
                    *(row.citation or []),
                    {"type": "playbook_deviation", "playbook_deviation_id": deviation.id},
                ]
    db.commit()
    return result


# ---- which issues still need a change ---------------------------------------------------


def _severity_rank(severity: str | None) -> int:
    value = (severity or "").lower()
    if value in ("critical", "high"):
        return 0
    if value == "medium":
        return 1
    if value == "low":
        return 2
    return 3


def _has_change(deviation: PlaybookDeviation, edits: list[ContractEdit], current_version_id: str | None) -> bool:
    """Whether the Review tab already shows a change for this issue. This mirrors the
    frontend (lib/suggestions.ts: editsForIssue, buildSuggestions): a change tied to the
    issue, or an older playbook change that only shares its text as its rationale; and a
    rejected change made for an older version of the contract says nothing about the
    issue as it stands, so it does not count."""
    mine = [e for e in edits if redline_edits.paired_deviation_id(e.citation) == deviation.id]
    if not mine:
        mine = [
            e
            for e in edits
            if redline_edits.paired_deviation_id(e.citation) is None
            and e.edit_type == "playbook_redline"
            and e.rationale == deviation.issue
        ]
    def stale(edit: ContractEdit) -> bool:  # frontend editState: only when a current version is known
        return bool(current_version_id) and edit.contract_version_id != current_version_id

    return any(not (e.status == "rejected" and stale(e)) for e in mine)


def issues_needing_change(
    db: Session, *, contract: Contract, playbook_run_id: str | None = None
) -> list[PlaybookDeviation]:
    """Open issues of this contract with no change in the Review tab, most severe first.
    With ``playbook_run_id``, only the issues that review found; without it, the issues the
    Review tab lists (contracts.risk.current_open_findings), so "Draft fixes for all" never
    drafts for an older review's copy of a problem the tab no longer shows."""
    from app.contracts.risk import current_open_findings

    issues = current_open_findings(db, contract)
    if playbook_run_id:
        issues = [d for d in issues if d.playbook_run_id == playbook_run_id]
    if not issues:
        return []
    edits = list(
        db.scalars(
            select(ContractEdit).where(ContractEdit.org_id == contract.org_id, ContractEdit.contract_id == contract.id)
        ).all()
    )
    current = contract.current_authoritative_version_id
    waiting = [d for d in issues if not _has_change(d, edits, current)]
    return sorted(waiting, key=lambda d: _severity_rank(d.severity))  # stable: ties keep their order


# ---- starting the job ---------------------------------------------------------------------


def _live_job(db: Session, *, contract: Contract) -> JobRun | None:
    """A drafting job for this contract that is queued or running, and not dead."""
    return db.scalar(
        select(JobRun)
        .where(
            JobRun.org_id == contract.org_id,
            JobRun.job_type == JOB_TYPE,
            JobRun.resource_type == "contract",
            JobRun.resource_id == contract.id,
            JobRun.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
            JobRun.created_at > utcnow() - STALE_AFTER,
        )
        .order_by(JobRun.created_at.desc())
        .limit(1)
    )


def latest_job(db: Session, *, contract: Contract) -> JobRun | None:
    return db.scalar(
        select(JobRun)
        .where(
            JobRun.org_id == contract.org_id,
            JobRun.job_type == JOB_TYPE,
            JobRun.resource_type == "contract",
            JobRun.resource_id == contract.id,
        )
        .order_by(JobRun.created_at.desc())
        .limit(1)
    )


def can_draft(db: Session, *, user: User) -> bool:
    return has_permission(org_access.effective_permission_values(db, user=user), "contract:redline")


def enqueue_issue_fix_drafting(
    db: Session,
    *,
    contract: Contract,
    actor: User,
    playbook_run_id: str | None = None,
    automatic: bool,
) -> JobRun | None:
    """Queue the job that drafts a change for each issue that has none, or return the
    one already queued or running. None when there is nothing to do, when the person
    may not redline, or (automatic only) when the setting is off.

    ``automatic`` is the review finishing on its own; a person clicking "Draft fixes for
    all" is not automatic, so the setting never blocks it. Commits."""
    if automatic and not settings.auto_draft_issue_fixes:
        return None
    if not can_draft(db, user=actor):
        return None
    live = _live_job(db, contract=contract)
    if live is not None:
        return live
    waiting = issues_needing_change(db, contract=contract, playbook_run_id=playbook_run_id if automatic else None)
    if not waiting:
        return None
    jobs = JobsService(db)
    job = jobs.create_job(
        org_id=contract.org_id,
        job_type=JOB_TYPE,
        resource_type="contract",
        resource_id=contract.id,
        created_by_user_id=actor.id,
        # One automatic job per review: asking twice for the same run is the same job.
        idempotency_key=f"{JOB_TYPE}:{playbook_run_id}" if automatic and playbook_run_id else None,
        metadata={
            "trigger": "review" if automatic else "manual",
            "playbook_run_id": playbook_run_id if automatic else None,
            # What the job will take on, so the tab can say "0 of 3" before the worker starts it.
            # The job counts again when it starts (an issue may have been handled by then).
            "total": min(len(waiting), max(0, settings.auto_draft_max_issues)),
            "left_for_later": max(0, len(waiting) - max(0, settings.auto_draft_max_issues)),
        },
    )
    db.commit()
    if job.status != JobStatus.QUEUED or job.celery_task_id:
        return job  # an earlier job for this same review: never run it again from here
    try:
        jobs.dispatch_job(job=job)
        db.commit()
    except Exception:
        # The row is saved and still queued: the periodic reclaim (jobs.tasks.reclaim_stale_jobs)
        # hands any queued job that was never dispatched to the worker, so a broker hiccup
        # delays the drafting; it doesn't lose it.
        db.rollback()
        logger.warning("could not dispatch fix drafting job %s; it stays queued", job.id, exc_info=True)
    return job


def start_after_review(
    db: Session, *, contract: Contract, actor_user_id: str | None, playbook_run_id: str | None
) -> JobRun | None:
    """Called once a playbook review is saved. Returns the job, or None when there is nothing to
    draft, the setting is off, or it could not be queued. Never raises: the review has succeeded
    whether or not the drafting could be queued, and a person can always ask for it."""
    if actor_user_id is None or playbook_run_id is None:
        return None
    try:
        actor = db.get(User, actor_user_id)
        if actor is None:
            return None
        return enqueue_issue_fix_drafting(
            db, contract=contract, actor=actor, playbook_run_id=playbook_run_id, automatic=True
        )
    except Exception:
        db.rollback()
        logger.warning("could not queue fix drafting for contract %s", contract.id, exc_info=True)
        return None


# ---- what the Review tab is told ------------------------------------------------------------


def job_status(job: JobRun) -> dict[str, Any]:
    """The job as the Review tab shows it: counts only, never an internal error text."""
    meta = job.metadata_json or {}
    state = str(job.status)
    stale = state in (JobStatus.QUEUED, JobStatus.RUNNING) and job.created_at < utcnow() - STALE_AFTER
    return {
        "job_id": job.id,
        "status": "failed" if stale else state,
        "trigger": meta.get("trigger"),
        "total": int(meta.get("total", 0)),
        "done": int(meta.get("done", 0)),
        "drafted": int(meta.get("drafted", 0)),
        "no_change": int(meta.get("no_change", 0)),
        "failed": int(meta.get("failed", 0)),
        "left_for_later": int(meta.get("left_for_later", 0)),
        "stopped_early": bool(meta.get("stopped_early", False)),
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


# ---- the job ------------------------------------------------------------------------------------


async def run_issue_fix_job(db: Session, job: JobRun) -> None:
    """Draft a change for each issue that has none, one request at a time.

    Safe to run again: an issue that has a change by then is skipped, so a job that is
    redelivered, reclaimed after its worker died, or run while someone clicks a button
    by hand does not draft twice. Each issue is saved as it is done, so a stop part-way
    keeps everything drafted so far. One issue failing never stops the others, except
    that several failures in a row end the job (see MAX_CONSECUTIVE_FAILURES)."""
    from celery.exceptions import SoftTimeLimitExceeded

    contract = db.get(Contract, job.resource_id)
    user = db.get(User, job.created_by_user_id) if job.created_by_user_id else None
    if contract is None or contract.deleted_at is not None:
        raise RuntimeError("Contract not found for fix drafting")
    if user is None or not can_draft(db, user=user):
        _finish(db, job, JobStatus.FAILED, error="The person who started this may not redline this contract.")
        return

    run_id = (job.metadata_json or {}).get("playbook_run_id")
    waiting = issues_needing_change(db, contract=contract, playbook_run_id=run_id)
    cap = max(0, settings.auto_draft_max_issues)
    todo, later = waiting[:cap], waiting[cap:]
    meta: dict[str, Any] = {
        **(job.metadata_json or {}),
        "total": len(todo),
        "left_for_later": len(later),
        "done": 0,
        "drafted": 0,
        "no_change": 0,
        "failed": 0,
        "stopped_early": False,
        "issues": [],
    }
    _save(db, job, meta)

    deadline = time.monotonic() + JOB_BUDGET_SECONDS
    in_a_row = 0
    todo_ids = [d.id for d in todo]
    for index, deviation_id in enumerate(todo_ids):
        db.refresh(job)
        if job.status == JobStatus.CANCELLED:
            return
        if time.monotonic() > deadline or in_a_row >= MAX_CONSECUTIVE_FAILURES:
            meta["stopped_early"] = True
            break
        deviation = db.get(PlaybookDeviation, deviation_id)
        db.refresh(deviation)
        db.refresh(contract)
        outcome, reason = "skipped", None
        if deviation.status in OPEN_ISSUE_STATUSES and deviation in issues_needing_change(db, contract=contract):
            try:
                result = await redline_with_ai(
                    db, contract=contract, user=user, instructions=fix_instruction(deviation), deviation=deviation
                )
                outcome = "drafted" if int(result.get("edits") or 0) > 0 else "no_change"
                in_a_row = 0
            except SoftTimeLimitExceeded:
                raise
            except HTTPException as exc:
                db.rollback()
                # 422: the model found no wording to change. That is an answer, not a failure.
                outcome = "no_change" if exc.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT else "failed"
                reason = str(exc.detail)[:200]
                in_a_row = 0 if outcome == "no_change" else in_a_row + 1
            except Exception as exc:
                db.rollback()
                logger.warning("drafting a fix for issue %s failed", deviation_id, exc_info=True)
                outcome, reason = "failed", f"{exc.__class__.__name__}: {exc}"[:200]
                in_a_row += 1
        meta["done"] = index + 1
        if outcome in ("drafted", "no_change", "failed"):
            meta[outcome] += 1
        meta["issues"].append({"id": deviation_id, "outcome": outcome, "reason": reason})
        _save(db, job, meta, progress=5 + int(90 * meta["done"] / max(1, len(todo_ids))))

    attempted = meta["drafted"] + meta["no_change"] + meta["failed"]
    if attempted > 0 and meta["failed"] == attempted:
        _finish(db, job, JobStatus.FAILED, meta=meta, error="The AI could not draft a change for any issue.")
    else:
        _finish(db, job, JobStatus.SUCCEEDED, meta=meta)
    if meta["drafted"]:
        write_timeline_event(
            db,
            org_id=contract.org_id,
            resource_type="contract",
            resource_id=contract.id,
            event_type="contract.issue_fixes_drafted",
            title=f"AI drafted a suggested change for {meta['drafted']} playbook issue{'' if meta['drafted'] == 1 else 's'}",
            actor_user_id=user.id,
            job_id=job.id,
            details={k: meta[k] for k in ("trigger", "total", "drafted", "no_change", "failed", "left_for_later")},
        )
        db.commit()


def _save(db: Session, job: JobRun, meta: dict[str, Any], *, progress: int | None = None) -> None:
    job.metadata_json = dict(meta)  # a new dict: SQLAlchemy doesn't see changes made inside a JSON value
    if progress is not None:
        job.progress = max(job.progress or 0, progress)
    db.commit()


def _finish(
    db: Session, job: JobRun, state: JobStatus, *, meta: dict[str, Any] | None = None, error: str | None = None
) -> None:
    if meta is not None:
        job.metadata_json = dict(meta)
    job.status = state
    job.progress = 100
    job.finished_at = utcnow()
    job.error_message = error
    job.error_stack = None
    db.commit()
