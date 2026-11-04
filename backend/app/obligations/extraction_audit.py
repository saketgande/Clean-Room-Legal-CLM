"""Does this contract have a real obligation extraction for the version it is on right now?

One function answers it, so the lifecycle row, the Retry button and anything that comes later (a sweep, a workflow
note) cannot disagree. It reads `job_run` only: a succeeded job is set only after a schema-valid answer was stored,
while the skill-run table cannot say "it ran" (a carried-forward answer writes none, and a failed write leaves one
"running" for ever). Nothing is stored: the state is derived on read.

  no_text     no current version, no text snapshot, or a snapshot with no text in it: there is nothing to read
  done        a job succeeded and live obligations exist
  none_found  a job succeeded and found nothing. A valid final answer, never a failure: a contract can have none
  running     a job is queued (recently) or running (inside its lease)
  failed      jobs exist for this version and none succeeded; a job queued for hours (lost) or running past its
              lease (its worker died) counts as failed
  never       no job for this version and no live obligations
  stale       no job for this version, but live obligations exist from an older one

The one blind spot is a first run that returns an empty list for a contract that really has duties: no status can
reveal that. `none_found` carries the model's own note so a person can judge, and the golden set measures how often
it happens.

The reason shown to people is the job's own plain `failure.reason` (app/ai/outcomes.py) or a fixed sentence. It is
never `error_message`: jobs written before that field was made plain can hold the contract's own words.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.enums import JobStatus

JOB_TYPE = "obligation_extraction"

# A job still "queued" after this long was lost (the message never reached a worker, or the worker died before
# starting it). ponytail: a constant; make it a setting if it ever needs tuning.
LOST_AFTER = timedelta(hours=2)

State = Literal["no_text", "done", "none_found", "running", "failed", "never", "stale"]


@dataclass(frozen=True)
class JobFact:
    id: str
    status: str
    attempt_count: int
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    reason: str | None  # the job's own plain failure.reason, when it has one
    updated_at: datetime | None = None  # a re-queued job is new in the queue: its age counts from here, not creation
    retryable: bool | None = None  # the job's own failure.retryable; None for a job that has none (an older one)


@dataclass(frozen=True)
class ExtractionState:
    state: State
    live_obligations: int = 0
    attempts: int = 0  # how many times the worker really started a job for this version
    last_failed_at: datetime | None = None
    reason: str | None = None  # plain sentence, for failed / never / no_text
    retryable: bool = True  # False when the last failure says another try fails the same way (cut off, refused...)
    note: str | None = None  # the model's own note, for none_found
    poor_text: bool = False  # the text was read poorly (none_found: say so)
    # For failed: when it stopped being worth waiting for. Always set (a lost or dead job has no finished_at: it is
    # when it was given up on), so a caller never compares a missing time. `cancelled`: the newest job was cancelled
    # on purpose, which is not a failure to report.
    failed_since: datetime | None = None
    cancelled: bool = False


def dead(job: JobFact | object, now: datetime, lease: timedelta) -> str | None:
    """Why a job that is not finished will never finish by itself, or None while it still might. `lost`: queued for
    hours. `died`: running past its lease. Works on a JobFact or a JobRun (same field names)."""
    status = job.status
    if status == JobStatus.QUEUED:
        # How long it has waited in the queue: from when it was last queued (a restart re-queues the same row, whose
        # created_at may be days old), not from when the row was made.
        queued = max((t for t in (job.created_at, getattr(job, "updated_at", None)) if t is not None), default=None)
        return "lost" if queued is not None and queued < now - LOST_AFTER else None
    if status == JobStatus.RUNNING:
        began = job.started_at or job.created_at
        return "died" if began is not None and began < now - lease else None
    return None


_GENERIC = {
    "lost": "The reading was queued but never started.",
    "died": "The worker stopped before finishing the reading.",
    "cancelled": "The reading was cancelled.",
    "failed": "The last attempt did not finish.",
}


def decide(
    *, has_text: bool, jobs: list[JobFact], live_obligations: int, now: datetime, lease: timedelta
) -> ExtractionState:
    """The state from plain facts (pure: no database, no clock). See the module docstring."""
    if not has_text:
        return ExtractionState("no_text", live_obligations, reason="The contract has no readable text yet.")
    attempts = sum(j.attempt_count or 0 for j in jobs)
    if any(j.status == JobStatus.SUCCEEDED for j in jobs):
        return ExtractionState("done" if live_obligations else "none_found", live_obligations, attempts)
    if not jobs:
        if live_obligations:
            return ExtractionState("stale", live_obligations)
        return ExtractionState("never", reason="Obligations have not been read for this version.")

    still_possible = [j for j in jobs if j.status in (JobStatus.QUEUED, JobStatus.RUNNING) and not dead(j, now, lease)]
    if still_possible:
        return ExtractionState("running", live_obligations, attempts)

    # Failed: say why from the latest job that ended badly.
    ended = sorted(jobs, key=lambda j: j.finished_at or j.created_at or now)
    last = ended[-1]
    why = dead(last, now, lease) or ("cancelled" if last.status == JobStatus.CANCELLED else "failed")
    failed_at = max((j.finished_at for j in jobs if j.finished_at is not None), default=None)
    queued = max((t for t in (last.created_at, last.updated_at) if t is not None), default=None)
    given_up = {
        "lost": queued + LOST_AFTER if queued is not None else None,
        "died": (last.started_at or last.created_at) + lease if (last.started_at or last.created_at) else None,
    }.get(why)
    return ExtractionState(
        "failed", live_obligations, attempts, last_failed_at=failed_at, reason=last.reason or _GENERIC[why],
        retryable=last.retryable is not False,
        failed_since=last.finished_at or given_up or last.updated_at or last.created_at or now,
        cancelled=last.status == JobStatus.CANCELLED,
    )


def has_text_clause():
    """SQL for "this snapshot has something to read": a character that is not whitespace. Asked of the database in BOTH
    loaders (a Python .strip() and Postgres' trim() disagree about newlines and tabs, and a blank scan is stored as
    newlines), and the regex stops at the first such character instead of measuring a long text."""
    from app.contract_files.models import ContractTextSnapshot

    return ContractTextSnapshot.text.op("~")(r"\S")


def version_jobs(db: Session, *, contract, version_id: str) -> list:
    """Every obligation_extraction JobRun of this contract for this version, whatever its key suffix (the plain key
    from upload and activation, ':manual' from the Retry button, ':assistant'). Oldest first."""
    from app.jobs.models import JobRun

    rows = db.scalars(
        select(JobRun)
        .where(JobRun.org_id == contract.org_id, JobRun.job_type == JOB_TYPE, JobRun.resource_id == contract.id)
        .order_by(JobRun.created_at, JobRun.id)
    ).all()
    return [j for j in rows if (j.metadata_json or {}).get("contract_version_id") == version_id]


def _fact(job) -> JobFact:
    failure = (job.metadata_json or {}).get("failure") or {}
    return JobFact(job.id, job.status, job.attempt_count or 0, job.created_at, job.started_at, job.finished_at,
                   failure.get("reason") if isinstance(failure.get("reason"), str) else None,
                   getattr(job, "updated_at", None),
                   failure.get("retryable") if isinstance(failure.get("retryable"), bool) else None)


def extraction_state(db: Session, contract, *, now: datetime | None = None) -> ExtractionState:
    """The extraction state of `contract` for the version it is on right now."""
    from app.contract_files.models import ContractTextSnapshot, ContractVersion
    from app.jobs.tasks import JOB_LEASE
    from app.obligations.models import Obligation

    live = db.scalar(
        select(func.count(Obligation.id)).where(Obligation.contract_id == contract.id, Obligation.deleted_at.is_(None))
    ) or 0
    version = db.get(ContractVersion, contract.current_authoritative_version_id) if contract.current_authoritative_version_id else None
    snapshot = db.get(ContractTextSnapshot, version.text_snapshot_id) if version is not None and version.text_snapshot_id else None
    if version is None or snapshot is None or not db.scalar(select(has_text_clause()).where(ContractTextSnapshot.id == snapshot.id)):
        return decide(has_text=False, jobs=[], live_obligations=live, now=now or utcnow(), lease=JOB_LEASE)
    jobs = version_jobs(db, contract=contract, version_id=version.id)
    state = decide(has_text=True, jobs=[_fact(j) for j in jobs], live_obligations=live, now=now or utcnow(),
                   lease=JOB_LEASE)
    if state.state == "none_found":
        state = replace(state, note=_model_note(db, [j for j in jobs if j.status == JobStatus.SUCCEEDED]),
                        poor_text=snapshot.validation_status == "needs_review")
    return state


def extraction_states(db: Session, contracts, *, now: datetime | None = None) -> dict[str, ExtractionState]:
    """`extraction_state` for many contracts at once (a handful of queries, not a few per contract), for a portfolio
    question or the daily check. The same `decide`, so it can never disagree with the single answer; it skips the model's
    note (it matters only to a person reading one contract's row)."""
    from collections import defaultdict

    from app.contract_files.models import ContractTextSnapshot, ContractVersion
    from app.jobs.models import JobRun
    from app.jobs.tasks import JOB_LEASE
    from app.obligations.models import Obligation

    contracts = list(contracts)
    if not contracts:
        return {}
    ids, orgs, now = [c.id for c in contracts], {c.org_id for c in contracts}, now or utcnow()
    live = dict(db.execute(
        select(Obligation.contract_id, func.count(Obligation.id))
        .where(Obligation.org_id.in_(orgs), Obligation.contract_id.in_(ids), Obligation.deleted_at.is_(None))
        .group_by(Obligation.contract_id)
    ).all())
    version_ids = {c.current_authoritative_version_id for c in contracts if c.current_authoritative_version_id}
    versions = {v.id: v for v in db.scalars(select(ContractVersion).where(ContractVersion.id.in_(version_ids)))} if version_ids else {}
    snapshot_ids = {v.text_snapshot_id for v in versions.values() if v.text_snapshot_id}
    # A snapshot with no text in it is no text (a scan that read nothing): there is nothing to read, so nothing to pay for.
    has_snapshot = set(db.scalars(
        select(ContractTextSnapshot.id).where(ContractTextSnapshot.id.in_(snapshot_ids), has_text_clause()))) if snapshot_ids else set()
    poorly_read = set(db.scalars(
        select(ContractTextSnapshot.id).where(ContractTextSnapshot.id.in_(snapshot_ids),
                                              ContractTextSnapshot.validation_status == "needs_review"))) if snapshot_ids else set()
    jobs: dict[str, list] = defaultdict(list)
    for job in db.scalars(
        select(JobRun)
        .where(JobRun.org_id.in_(orgs), JobRun.job_type == JOB_TYPE, JobRun.resource_id.in_(ids))
        .order_by(JobRun.created_at, JobRun.id)
    ):
        jobs[job.resource_id].append(job)
    out: dict[str, ExtractionState] = {}
    for contract in contracts:
        version = versions.get(contract.current_authoritative_version_id)
        if version is None or version.text_snapshot_id not in has_snapshot:
            out[contract.id] = decide(has_text=False, jobs=[], live_obligations=live.get(contract.id, 0), now=now,
                                      lease=JOB_LEASE)
            continue
        mine = [j for j in jobs[contract.id] if (j.metadata_json or {}).get("contract_version_id") == version.id]
        state = decide(has_text=True, jobs=[_fact(j) for j in mine], live_obligations=live.get(contract.id, 0),
                       now=now, lease=JOB_LEASE)
        if state.state == "none_found":
            state = replace(state, poor_text=version.text_snapshot_id in poorly_read)
        out[contract.id] = state
    return out


# The states in which a contract that is Active has no (or no current) obligations on file because they were not read.
NOT_READ_STATES = ("failed", "never", "no_text", "stale")


_BATCH = 500  # contracts per round of queries: keeps the IN lists short, never the answer


def needs_reading(db: Session, user) -> list[tuple]:
    """The Active contracts this person may open whose obligations are not (or no longer) properly read: failed,
    never read, no readable text, from an earlier version, or being read right now. (contract, state) pairs, oldest
    first. Every Active contract is checked: stopping at a first few hundred before filtering would drop the newest,
    the ones most likely never to have been read, and the answer would still say none.

    ponytail: reads the id of every Active contract once and checks them `_BATCH` at a time. Fine to tens of
    thousands; past that a daily sweep (the plan's slice 2) is the right home for this, not a chat tool."""
    from app.contracts.access import accessible_contract_filter
    from app.contracts.models import Contract
    from app.obligations.service import live_contract_ids

    rows = db.execute(
        select(Contract.id, Contract.org_id, Contract.current_authoritative_version_id)
        .where(
            Contract.org_id == user.org_id,
            Contract.deleted_at.is_(None),
            Contract.lifecycle_stage == "active",
            accessible_contract_filter(user),
            Contract.id.in_(live_contract_ids()),
        )
        .order_by(Contract.created_at, Contract.id)
    ).all()
    out: list[tuple] = []
    for start in range(0, len(rows), _BATCH):
        batch = rows[start:start + _BATCH]
        states = extraction_states(db, batch)
        wanted = [r.id for r in batch if states[r.id].state in (*NOT_READ_STATES, "running")]
        if wanted:
            found = {c.id: c for c in db.scalars(select(Contract).where(Contract.id.in_(wanted)))}
            out.extend((found[i], states[i]) for i in wanted)
    return out


def _model_note(db: Session, succeeded: list) -> str | None:
    """The note the model wrote with an empty answer ("no operative obligations found..."), from its skill run."""
    from app.ai.models import AISkillRun

    for job in reversed(succeeded):
        run = db.scalar(select(AISkillRun).where(AISkillRun.job_id == job.id).order_by(AISkillRun.created_at.desc()))
        note = ((run.output_payload or {}) if run is not None else {}).get("extraction_notes")
        if isinstance(note, str) and note.strip():
            return note.strip()[:300]
    return None
