"""Did a contract's automatic AI review finish, and how to run it again.

After a draft is made, a counterparty revision arrives, or accepted edits make a new
version, the contract is flagged ``auto_review_pending`` and the background job that
reads its clauses (``clause_extraction``) ends by running the matching playbook review
(``jobs.tasks._maybe_auto_review``). That job lives on a worker. If the worker stops
part-way (a restart, a crash), nothing clears the flag, so the contract just looks
"still being reviewed" and nothing on the page says otherwise.

This module answers one question from facts already in the database: is a review that
was asked for still being worked on, or did it stop? And it holds the one action that
fixes it: queue the review again, as a job of its own (``contract_review``).

What it can and cannot know. The worker sends no heartbeat, so a review that is only
slow cannot be told from one whose worker died. A review counts as interrupted when
its job failed, when there is no job at all, or when it has not finished within
``STALL_SECONDS`` of starting. Celery stops any task at 15 minutes (``jobs.celery_app``),
so past that a "running" job is certainly dead; between 10 and 15 minutes it is
probably dead. Retrying is safe either way: at worst a review that was only slow is
paid for twice (see ``retry_review``). A heartbeat would replace the guess with a fact.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.models import Contract
from app.core import org_access
from app.core.audit import write_timeline_event
from app.core.database import utcnow
from app.core.enums import JobStatus
from app.core.rbac import has_permission
from app.jobs.models import JobRun
from app.jobs.service import JobsService

logger = logging.getLogger(__name__)

# The flag the review is asked for with (set where a draft, a revision or accepted edits make
# new text; cleared when the review ends). ``auto_review_attempt`` names the job that owns the
# review once a person retried it, so the older job, if it is still alive, leaves it alone.
PENDING_KEY = "auto_review_pending"
ATTEMPT_KEY = "auto_review_attempt"
# Why the review was asked for, when that changes what it does. AFTER_ACCEPT: the person accepted
# changes, so the new text is checked again and its findings listed, but no new redlines or AI fixes
# are written for them to decide (that kept the Review tab from ever emptying). Any other review
# (a fresh draft, a counterparty revision) has no reason and proposes changes as before.
REASON_KEY = "auto_review_reason"
AFTER_ACCEPT = "accepted_edits"
RETRY_JOB_TYPE = "contract_review"
# Jobs that carry the review: clause extraction runs it as its last step; a retry is its own job.
REVIEW_JOB_TYPES = ("clause_extraction", RETRY_JOB_TYPE)
# Longest a requested review may go unfinished before it counts as interrupted. The workflow's
# AI review step waits the same time (workflows.service._REVIEW_WAIT_SECONDS).
STALL_SECONDS = 600

INTERRUPTED_NOTE = "The AI review didn't finish — it was interrupted before it could. Retry it."


def request_review(contract: Contract, *, after: str | None = None) -> None:
    """Flag the contract for an automatic review once its clauses are read (``jobs.tasks._maybe_auto_review``).
    ``after`` says why when that changes what the review does (AFTER_ACCEPT); a request without one clears
    a reason left by an earlier request that has not run yet. Does not commit."""
    meta = dict(contract.metadata_json or {})
    meta[PENDING_KEY] = True
    if after:
        meta[REASON_KEY] = after
    else:
        meta.pop(REASON_KEY, None)
    contract.metadata_json = meta


def proposes_changes(contract: Contract) -> bool:
    """Whether the pending review writes redlines and AI-drafted fixes (False right after accepted changes)."""
    return (contract.metadata_json or {}).get(REASON_KEY) != AFTER_ACCEPT


def can_retry(db: Session, *, user) -> bool:
    """Retrying makes a playbook run and tracked-change redlines, so it needs both rights."""
    values = org_access.effective_permission_values(db, user=user)
    return has_permission(values, "contract:redline") and has_permission(values, "playbook:run")


def _began(job: JobRun) -> datetime | None:
    if job.status == JobStatus.RUNNING:
        return job.started_at or job.created_at
    if job.status == JobStatus.SUCCEEDED:
        # Clause extraction marks itself done BEFORE the review it then runs, so the review began here.
        return job.finished_at or job.created_at
    return job.created_at


def _qualifying_review_exists(db: Session, contract: Contract) -> bool:
    """A review the risk band already counts exists for this exact text (contracts.risk)."""
    from app.contracts.risk import contract_risk

    return bool(contract_risk(db, contract)["playbook_run_ids"])


def review_state(db: Session, contract: Contract, *, now: datetime | None = None) -> dict[str, Any] | None:
    """The contract's requested-but-unfinished AI review, or None when nothing is pending.

    ``{"state": "running" | "interrupted", "reason": None | "failed" | "stalled" | "no_job",
    "since": when the attempt started, "job_id": the job it is on, "after": AFTER_ACCEPT when it
    follows accepted changes, else None}``. A pending flag left behind
    by a review that did finish (the worker stopped after saving it, before clearing the flag)
    is not a pending review: it reads None."""
    if not (contract.metadata_json or {}).get(PENDING_KEY):
        return None
    after = (contract.metadata_json or {}).get(REASON_KEY)
    now = now or utcnow()
    job = db.scalar(
        select(JobRun)
        .where(
            JobRun.org_id == contract.org_id,
            JobRun.resource_type == "contract",
            JobRun.resource_id == contract.id,
            JobRun.job_type.in_(REVIEW_JOB_TYPES),
        )
        .order_by(JobRun.created_at.desc(), JobRun.id.desc())
        .limit(1)
    )
    if job is None:
        # Flagged but never given to a worker. Give the request itself the same time to start one.
        began, job_id, reason = contract.updated_at or contract.created_at, None, "no_job"
        interrupted = began is not None and now - began > timedelta(seconds=STALL_SECONDS)
    elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
        began, job_id, reason, interrupted = job.finished_at or job.created_at, job.id, "failed", True
    else:
        began, job_id, reason = _began(job), job.id, "stalled"
        interrupted = began is not None and now - began > timedelta(seconds=STALL_SECONDS)
    if not interrupted:
        return {"state": "running", "reason": None, "since": began, "job_id": job_id, "after": after}
    if _qualifying_review_exists(db, contract):
        return None
    return {"state": "interrupted", "reason": reason, "since": began, "job_id": job_id, "after": after}


def retry_review(db: Session, *, contract: Contract, actor) -> dict[str, Any]:
    """Run the contract's AI review again as its own job. Commits.

    Returns ``{"status": ..., "job_id": ...}`` where status is
      * ``queued``          a new job was started;
      * ``already_running`` a review is still in progress (a second click, or a second tab);
      * ``not_needed``      a review of this exact text already exists, so a second would be paid for
                            the same answer (a pending flag left behind is cleared);
      * ``no_playbook``     no playbook covers this contract type, so there is nothing to review it against.

    The review is the same function the automatic review runs (``jobs.tasks._maybe_auto_review``),
    so the result is the same: a playbook run and, unless it follows accepted changes (the reason is
    kept), tracked-change redlines and the job that drafts fixes for the issues without wording. The job takes over the review by naming itself in
    ``auto_review_attempt``; the older job, if it was only slow, then skips its review. If it had
    already begun one, the review runs twice, and that is the worst case."""
    if not can_retry(db, user=actor):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Restarting the AI review needs permission to redline and to run playbooks."
        )
    # One retry at a time per contract: a second request waits here, then sees the first one's job.
    db.execute(select(Contract.id).where(Contract.id == contract.id).with_for_update())
    db.refresh(contract)
    state = review_state(db, contract)
    if state is not None and state["state"] == "running":
        return {"status": "already_running", "job_id": state["job_id"]}

    from app.contracts.risk import contract_risk

    risk = contract_risk(db, contract)
    meta = dict(contract.metadata_json or {})
    if risk["playbook_id"] is None:
        return {"status": "no_playbook", "job_id": None}
    if risk["playbook_run_ids"]:
        left_over = meta.pop(PENDING_KEY, None) is not None
        left_over = meta.pop(ATTEMPT_KEY, None) is not None or left_over
        left_over = meta.pop(REASON_KEY, None) is not None or left_over
        if left_over:
            contract.metadata_json = meta
            db.commit()
        return {"status": "not_needed", "job_id": None}

    jobs = JobsService(db)
    job = jobs.create_job(
        org_id=contract.org_id,
        job_type=RETRY_JOB_TYPE,
        resource_type="contract",
        resource_id=contract.id,
        created_by_user_id=actor.id,
        metadata={"contract_version_id": contract.current_authoritative_version_id, "trigger": "retry"},
    )
    db.flush()
    meta[PENDING_KEY] = True
    meta[ATTEMPT_KEY] = job.id
    contract.metadata_json = meta
    write_timeline_event(
        db,
        org_id=contract.org_id,
        resource_type="contract",
        resource_id=contract.id,
        event_type="contract.auto_review_retried",
        title="AI review restarted",
        actor_user_id=actor.id,
        job_id=job.id,
        details={"after": (state or {}).get("reason") or "not_pending"},
    )
    db.commit()
    try:
        jobs.dispatch_job(job=job)
        db.commit()
    except Exception:
        # The row is saved and still queued: the periodic reclaim (jobs.tasks.reclaim_stale_jobs)
        # hands a queued job that was never dispatched to a worker, so a broker hiccup delays the
        # review; it doesn't lose it.
        db.rollback()
        logger.warning("could not dispatch review job %s; it stays queued", job.id, exc_info=True)
    return {"status": "queued", "job_id": job.id}
