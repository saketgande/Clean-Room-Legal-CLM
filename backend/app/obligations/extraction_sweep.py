"""The daily check that every Active contract's obligations were really read.

`extraction_state` (extraction_audit.py) says, for each Active contract, whether the version it is on was read. Once a day
this sweep acts on that, and only that, in four ways:

  never        start ONE read (a paid call) of a contract nobody ever read: at most 6 a day, never past half of the day's
               AI budget, never while the last few it started all failed, and only when `obligation_audit_start_jobs` is on
  failed       tell the contract's owner ONCE, once the failure is settled (a transport error, a worker that died and a lost
               message all have another layer still trying: see app/core/retry_policy.py)
  no_text, stale, and a none-found read of poorly read text: tell the owner ONCE
  anything else  nothing

It NEVER re-runs a failed job: a person presses Retry (a machine re-runs only transport errors, Step 8). It never replaces
a stale contract's obligations. Admins get one digest a day, only when something happened.

The first run is a BASELINE and sends nothing per contract: it stores the problems that already exist (as
`(contract, version)` pairs, not as times, which a retry or the Jobs page's reaper would move) in an AdminSetting, sends
admins one note saying the check has started, and starts nothing. Those problems stay red on each contract's Active stage;
only problems that are new after that are pushed to people.

Who is told is checked silently (`_contract_visible_silently`: a wall or clearance denial writes an `access.denied` audit
row on the other check, for people who never tried to open anything). A notice carries a contract's title and the job's own
plain failure reason, never `error_message`. Locks are per contract and non-blocking (`pg_try_advisory_xact_lock` in a key
space of its own), never a lock on the Contract row: the audit chain's advisory lock is taken first by the code that edits
contracts, so a row lock here could deadlock with it.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import Role, User
from app.contract_files.models import ContractVersion
from app.contracts.models import Contract
from app.core.audit import write_audit_log, write_timeline_event
from app.core.config import settings
from app.core.database import utcnow
from app.core.enums import JobStatus, UserStatus
from app.core.models import AdminSetting
from app.intake.activity import _contract_visible_silently
from app.jobs.models import JobRun
from app.notifications.once import already_notified, notify_once
from app.obligations.extraction_audit import (
    JOB_TYPE,
    ExtractionState,
    extraction_state,
    extraction_states,
)
from app.obligations.service import live_contract_ids

logger = logging.getLogger(__name__)

SETTING_KEY = "obligation_audit_baseline"
SOURCE = "daily_audit"
SETTLE_AFTER = timedelta(hours=1)  # a failure is told only once nothing else is still working on it
MAX_JOBS_PER_DAY = 6  # half of the 12 worker slots; ponytail: stagger with a countdown or give the sweep its own queue
MAX_NOTICES_PER_OWNER = 10  # per run; the rest wait for tomorrow
BREAKER_FAILURES = 3  # this many of the sweep's own reads failed in 48 h and none finished: stop starting more
RUN_BUDGET = timedelta(minutes=10)  # wall clock for one org's run; Celery's soft limit is 12
_BATCH = 500
_LOCK_CONTRACT = 90902  # advisory-lock key space of this sweep (the audit chain uses one bigint key, so these cannot collide)
_LOCK_RUN = 90901
_LISTED_TITLES = 5
# What one read can spend at most (the largest input the skill sends plus its output limit), for the budget floor: a read
# that is queued has not spent anything yet, so the ones this run already queued are counted at this size.
READ_TOKENS_ESTIMATE = 120_000

_PROBLEM_STATES = ("failed", "no_text", "stale")

# Why starting stopped, when an admin should hear it. The ordinary reasons (switched off, not set up, today's limit, a low
# budget) are normal operation, and saying them every day would teach people to ignore the digest.
_BREAKER = "the last reads it started all failed"
_UNREACHABLE = "the queue could not be reached"
_WORTH_TELLING = (_BREAKER, _UNREACHABLE)


def problem_key(contract_id: str, version_id: str | None) -> str:
    return f"{contract_id}:{version_id or 'none'}"


def audit_action(state: ExtractionState, *, known: bool, clock: datetime | None, now: datetime) -> str:
    """What the sweep does about one contract. Pure.

    `start`: read it. `escalate`: tell its owner. `known`: a problem that already existed when the check began.
    `wait`: not settled yet, look again tomorrow. `skip`: nothing to do. `clock` is when the problem began (for a failure
    `state.failed_since`; for no_text and stale the version's creation); None counts as long ago."""
    if state.state == "never":
        return "start"
    poor_none = state.state == "none_found" and state.poor_text
    if state.state not in _PROBLEM_STATES and not poor_none:
        return "skip"
    if state.cancelled:
        return "skip"  # somebody cancelled it on purpose: not a failure to report
    if known:
        return "known"
    if state.state == "failed" and clock is not None and now - clock < SETTLE_AFTER:
        return "wait"
    return "escalate"


def _notice(state: ExtractionState, title: str) -> tuple[str, str, str]:
    """(event type, subject, body) for a problem, in plain sentences. The closing sentence says only what the contract's
    Active-stage row really offers: Retry for a failure another try can fix, Re-read for a stale one."""
    if state.state == "failed":
        if state.retryable:
            tail = "Open the contract's Active stage. If you see a Retry button, press it; if not, ask an administrator."
        else:
            tail = "Another try would fail the same way. Ask an administrator to look."
        return "extraction.failed", f"Obligations could not be read: {title}", f"{state.reason} {tail}"
    if state.state == "stale":
        return (
            "extraction.stale", f"Obligations are from an earlier version: {title}",
            ("The document changed after its obligations were read. Open the contract's Active stage and press "
             "Re-read if the new text should be tracked."))
    if state.state == "no_text":
        return (
            "extraction.no_text", f"Obligations cannot be read, the contract has no text: {title}",
            "The contract has no readable text, so its obligations cannot be tracked. Upload a readable copy.")
    return (
        "extraction.poor_text", f"No obligations found, but the text was read poorly: {title}",
        ("The text could not be read well, so an empty result may be wrong. Open the contract and check its "
         "obligations by hand."))


def _try_lock(db: Session, space: int, key: str) -> bool:
    return bool(db.scalar(text("SELECT pg_try_advisory_xact_lock(:space, hashtext(:key))"), {"space": space, "key": key}))


def _active_contracts(db: Session, org_id: str) -> list:
    return db.execute(
        select(Contract.id, Contract.org_id, Contract.title, Contract.owner_user_id, Contract.created_at,
               Contract.current_authoritative_version_id)
        .where(Contract.org_id == org_id, Contract.deleted_at.is_(None), Contract.lifecycle_stage == "active",
               Contract.id.in_(live_contract_ids()))
        .order_by(Contract.created_at, Contract.id)
    ).all()


def _states_and_clocks(db: Session, rows: list, now: datetime) -> tuple[dict, dict]:
    states: dict[str, ExtractionState] = {}
    created: dict[str, datetime] = {}
    for start in range(0, len(rows), _BATCH):
        batch = rows[start:start + _BATCH]
        states.update(extraction_states(db, batch, now=now))
        vids = [r.current_authoritative_version_id for r in batch if r.current_authoritative_version_id]
        if vids:
            created.update(dict(db.execute(
                select(ContractVersion.id, ContractVersion.created_at).where(ContractVersion.id.in_(vids))).all()))
    return states, created


def _clock(state: ExtractionState, row, version_created: dict) -> datetime | None:
    if state.state == "failed":
        return state.failed_since
    return version_created.get(row.current_authoritative_version_id) or row.created_at


def _read_baseline(db: Session, org_id: str) -> tuple[str, set[str] | None]:
    """("missing", None) before the first run; ("ok", the known problems); ("invalid", None) when the stored value is
    not ours (an admin can PUT any value to any setting key: that must never read as "first run" and mute the check)."""
    row = db.scalars(
        select(AdminSetting).where(AdminSetting.org_id == org_id, AdminSetting.key == SETTING_KEY)
        .order_by(AdminSetting.created_at, AdminSetting.id)
    ).first()
    if row is None:
        return "missing", None
    value = row.value
    if isinstance(value, dict) and value.get("v") == 1 and isinstance(value.get("known"), list):
        return "ok", {str(k) for k in value["known"]}
    return "invalid", None


def _admins(db: Session, org_id: str) -> list[User]:
    rows = db.scalars(
        select(User).join(User.roles).where(User.org_id == org_id, User.status == UserStatus.ACTIVE, Role.name == "admin")
    ).all()
    return list({u.id: u for u in rows}.values())  # one person holding the role in two units is one admin


def _skill_enabled(db: Session, org_id: str) -> bool:
    from app.ai.registry import skill_registry
    from app.ai.tool_policy import flag_value_is_enabled

    spec = skill_registry.get("obligation_extraction")
    if spec.feature_flag is None:
        return True
    setting = db.scalar(select(AdminSetting).where(AdminSetting.org_id == org_id, AdminSetting.key == spec.feature_flag))
    return spec.enabled_by_default if setting is None else flag_value_is_enabled(
        setting.value, default=spec.enabled_by_default)


def _may_start(db: Session, org_id: str, now: datetime, queued: int = 0) -> str | None:
    """None when the sweep may start a paid read now, else the plain reason it may not. `queued` is how many reads this
    run already started."""
    if not settings.obligation_audit_start_jobs:
        return "starting reads is switched off in settings"
    if not (settings.claude_api_key or settings.mock_claude):
        return "the AI service is not set up"
    if not _skill_enabled(db, org_id):
        return "the obligation reading skill is switched off"
    mine = (JobRun.org_id == org_id, JobRun.job_type == JOB_TYPE,
            JobRun.metadata_json["source"].as_string() == SOURCE)
    recent = dict(db.execute(
        select(JobRun.status, func.count(JobRun.id)).where(*mine, JobRun.created_at >= now - timedelta(hours=48))
        .group_by(JobRun.status)).all())
    if recent.get(JobStatus.FAILED, 0) >= BREAKER_FAILURES and not recent.get(JobStatus.SUCCEEDED, 0):
        return _BREAKER
    if _started_today(db, org_id, now) >= MAX_JOBS_PER_DAY:
        return "today's limit of reads is used"
    return _budget_reason(org_id, queued * READ_TOKENS_ESTIMATE)


def _started_today(db: Session, org_id: str, now: datetime) -> int:
    midnight = datetime(now.year, now.month, now.day, tzinfo=UTC)
    return db.scalar(select(func.count(JobRun.id)).where(
        JobRun.org_id == org_id, JobRun.job_type == JOB_TYPE, JobRun.metadata_json["source"].as_string() == SOURCE,
        JobRun.created_at >= midnight)) or 0


def _budget_reason(org_id: str, reserved: int = 0) -> str | None:
    """Background reads must not use up what interactive use needs: stop below half of the day's AI budget, counting what
    the reads already queued will spend (`reserved`), and when the budget cannot be read (a read that fails open for a chat
    answer must not for background spend)."""
    from app.ai import cost_guard

    cap = settings.claude_daily_token_cap_per_org
    if cap <= 0:
        return None
    left = cost_guard.tokens_left(org_id)
    if left is None:
        return "the day's AI budget could not be read"
    return None if left - reserved >= cap // 2 else "less than half of the day's AI budget is left"


def _start_job(db: Session, row, now: datetime) -> str:
    """Start one read of this contract's current version. "started", "undispatched" (committed, will be sent by the
    reclaim sweep), or why not."""
    from app.jobs.service import create_job, dispatch_job

    try:
        if not _try_lock(db, _LOCK_CONTRACT, row.id):
            db.rollback()
            return "busy"
        db.expire_all()  # what the batch read minutes ago is stale
        contract = db.get(Contract, row.id)
        if contract is None or contract.deleted_at is not None or contract.archived or contract.lifecycle_stage != "active":
            db.rollback()
            return "changed"
        if extraction_state(db, contract, now=now).state != "never":
            db.rollback()
            return "changed"  # an activation or a person got there first
        version = db.get(ContractVersion, contract.current_authoritative_version_id)
        job = create_job(
            db, org_id=contract.org_id, job_type=JOB_TYPE, resource_type="contract", resource_id=contract.id,
            created_by_user_id=None, idempotency_key=f"{JOB_TYPE}:{version.id}:{version.text_snapshot_id}",
            metadata={"contract_version_id": version.id, "text_snapshot_id": version.text_snapshot_id, "source": SOURCE})
        db.flush()
        job_id = job.id
        write_audit_log(
            db, action="obligation.extraction_requested", resource_type="contract", resource_id=contract.id,
            org_id=contract.org_id, actor_user_id=None,
            after={"job_id": job_id, "contract_version_id": version.id, "source": SOURCE})
        write_timeline_event(
            db, org_id=contract.org_id, resource_type="contract", resource_id=contract.id,
            event_type="contract.obligations_extraction_requested", title="Obligation extraction started by the daily check",
            actor_user_id=None, job_id=job_id, details={"job_id": job_id, "source": SOURCE})
        db.commit()  # the row must be visible before a worker looks for it
    except SoftTimeLimitExceeded:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        return "changed"
    except Exception:
        db.rollback()
        logger.exception("daily audit: could not queue a read for contract %s", row.id)
        return "error"
    try:
        dispatch_job(db, job=db.get(JobRun, job_id))
        db.commit()  # dispatch only sets the task id in memory
    except SoftTimeLimitExceeded:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.warning("daily audit: queued a read for contract %s but could not send it", row.id, exc_info=True)
        return "undispatched"
    return "started"


def _escalate_one(db: Session, row, now: datetime, admins: list[User], sent_to: dict[str, int]) -> tuple[str, int]:
    """Tell the owner (or, with no eligible owner, the admins who can open the contract) about this contract's problem,
    once. At most MAX_NOTICES_PER_OWNER new notices to any one person per run, owner or fallback admin alike: a person who
    left may own a hundred contracts. Returns (outcome, notices written)."""
    try:
        if not _try_lock(db, _LOCK_CONTRACT, row.id):
            db.rollback()
            return "busy", 0
        db.expire_all()
        contract = db.get(Contract, row.id)
        if contract is None or contract.deleted_at is not None or contract.archived or contract.lifecycle_stage != "active":
            db.rollback()
            return "changed", 0
        state = extraction_state(db, contract, now=now)
        version = db.get(ContractVersion, contract.current_authoritative_version_id) if contract.current_authoritative_version_id else None
        clock = state.failed_since if state.state == "failed" else (version.created_at if version else contract.created_at)
        if audit_action(state, known=False, clock=clock, now=now) != "escalate":
            db.rollback()
            return "changed", 0
        event_type, subject, body = _notice(state, contract.title)
        key = {"contract_id": contract.id, "version_id": version.id if version else "none"}
        owner = db.get(User, contract.owner_user_id) if contract.owner_user_id else None
        if owner is not None and not (owner.org_id == contract.org_id and owner.status == UserStatus.ACTIVE
                                      and _contract_visible_silently(db, owner, contract.id)):
            owner = None
        recipients = [owner] if owner is not None else [a for a in admins if _contract_visible_silently(db, a, contract.id)]
        written = deferred = 0
        for person in recipients:
            if sent_to.get(person.id, 0) >= MAX_NOTICES_PER_OWNER and not already_notified(
                    db, org_id=contract.org_id, user_id=person.id, event_type=event_type, key=key):
                deferred += 1  # a notice already sent earlier is not a new one: it does not count against the cap
                continue
            if notify_once(db, org_id=contract.org_id, user_id=person.id, event_type=event_type, subject=subject,
                           body=body, key=key):
                written += 1
                sent_to[person.id] = sent_to.get(person.id, 0) + 1
        db.commit()
        return ("told" if written else "deferred" if deferred else "already_told"), written
    except SoftTimeLimitExceeded:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("daily audit: could not notify about contract %s", row.id)
        return "error", 0


def _digest(db: Session, org_id: str, admins: list[User], now: datetime, *, told: list, started: int, deferred: int,
            held_back: str | None) -> int:
    """One note per admin for the run, naming only the contracts that admin can open. Returns how many were written."""
    if not (told or started or deferred or held_back):
        return 0
    written = 0
    for admin in admins:
        shown = [title for cid, title in told if _contract_visible_silently(db, admin, cid)][:_LISTED_TITLES]
        parts = []
        if told:
            many = len(told) != 1
            line = f"{len(told)} contract{'s' if many else ''} need{'' if many else 's'} attention"
            if shown:
                line += ": " + ", ".join(shown)
                if len(told) > len(shown):
                    line += f" and {len(told) - len(shown)} more"
            else:
                line += " (none of them ones you can open)"
            parts.append(line + ".")
        if started:
            parts.append(f"{started} unread contract{'s were' if started != 1 else ' was'} sent to be read.")
        if deferred:
            parts.append(f"{deferred} more will be told tomorrow (a limit per person).")
        if held_back:
            parts.append(f"No new reads were started: {held_back}.")
        subject = "Obligation check: " + (f"{len(told)} need attention" if told else "nothing new to fix")
        if notify_once(db, org_id=org_id, user_id=admin.id, event_type="extraction.digest", subject=subject,
                       body=" ".join(parts), key={"date": now.date().isoformat()}):
            written += 1
    db.commit()
    return written


def _baseline(db: Session, org_id: str, rows: list, states: dict, now: datetime) -> dict:
    """The first run: remember what is already wrong, tell admins once, start and send nothing else."""
    if not _try_lock(db, _LOCK_RUN, org_id):
        db.rollback()
        return {"skipped": "another run is making the baseline"}
    if _read_baseline(db, org_id)[0] != "missing":  # someone finished it while we took the lock
        db.rollback()
        return {"skipped": "baseline already made"}
    known = sorted(
        problem_key(r.id, r.current_authoritative_version_id) for r in rows
        if states[r.id].state in _PROBLEM_STATES or (states[r.id].state == "none_found" and states[r.id].poor_text))
    counts = {s: sum(1 for r in rows if states[r.id].state == s) for s in ("failed", "never", "stale", "no_text")}
    # is_secret: internal state, and a list of contract ids that GET /admin/settings would hand to every admin, walled or not.
    db.add(AdminSetting(org_id=org_id, key=SETTING_KEY, value={"v": 1, "since": now.isoformat(), "known": known},
                        is_secret=True))
    db.flush()
    told = 0
    if settings.obligation_audit_notify:
        body = (
            "Aegis now checks every day that each Active contract's obligations were read. Today "
            f"{counts['failed']} could not be read, {counts['never']} were never read, {counts['stale']} come from an "
            f"earlier version and {counts['no_text']} have no readable text. They stay red on each contract's Active stage; "
            "only problems that are new from now on are sent to people."
            + ("" if settings.obligation_audit_start_jobs else " Reading contracts nobody read is switched off in settings."))
        for admin in _admins(db, org_id):
            told += notify_once(db, org_id=org_id, user_id=admin.id, event_type="extraction.audit_started",
                                subject="Daily obligation check started", body=body, key={"audit": "started"})
    db.commit()
    return {"baseline": True, "known": len(known), "states": counts, "told": told}


def audit_extraction(db: Session, *, org_id: str, now: datetime | None = None) -> dict:
    """One org's daily check. Returns what it did (for monitoring and the tests)."""
    now = now or utcnow()
    if not (settings.obligation_audit_notify or settings.obligation_audit_start_jobs):
        return {"skipped": "switched off"}
    status, known = _read_baseline(db, org_id)
    if status == "invalid":
        logger.error("daily audit: the baseline setting %r holds a value this code did not write; skipping the run", SETTING_KEY)
        return {"skipped": "unreadable baseline setting"}
    rows = _active_contracts(db, org_id)
    states, version_created = _states_and_clocks(db, rows, now)
    if status == "missing":
        return _baseline(db, org_id, rows, states, now)

    out = {"scanned": len(rows), "start": 0, "escalate": 0, "known": 0, "wait": 0, "skip": 0, "started": 0, "told": 0,
           "deferred": 0, "digests": 0, "errors": 0, "partial": False, "held_back": None}
    plan: list[tuple] = []
    for row in rows:
        state = states[row.id]
        action = audit_action(state, known=problem_key(row.id, row.current_authoritative_version_id) in (known or set()),
                              clock=_clock(state, row, version_created), now=now)
        out[action] += 1
        if action in ("start", "escalate"):
            plan.append((row, action))
    deadline = time.monotonic() + RUN_BUDGET.total_seconds()

    # Starting paid reads: oldest first, the day's limits checked again before each one (they move as reads are started).
    held_back = None
    for row, action in plan:
        if action != "start":
            continue
        if time.monotonic() > deadline:
            out["partial"] = True
            break
        held_back = _may_start(db, org_id, now, out["started"])
        if held_back is not None:
            break
        result = _start_job(db, row, now)
        if result in ("started", "undispatched"):
            out["started"] += 1
        elif result == "error":
            out["errors"] += 1
        if result == "undispatched":
            held_back = _UNREACHABLE  # do not wait out a publish timeout for every remaining contract
            break
    out["held_back"] = held_back

    told: list[tuple[str, str]] = []
    if settings.obligation_audit_notify:
        admins = _admins(db, org_id)
        sent_to: dict[str, int] = {}
        for row, action in plan:
            if action != "escalate":
                continue
            if time.monotonic() > deadline:
                out["partial"] = True
                break
            outcome, written = _escalate_one(db, row, now, admins, sent_to)
            if outcome == "deferred":
                out["deferred"] += 1
            elif outcome == "error":
                out["errors"] += 1
            if written:
                out["told"] += written
                told.append((row.id, row.title))
        out["digests"] = _digest(db, org_id, admins, now, told=told, started=out["started"], deferred=out["deferred"],
                                 held_back=held_back if held_back in _WORTH_TELLING else None)
    return out


def audit_all_orgs(db: Session, *, now: datetime | None = None) -> list[dict]:
    """The beat task's body: every org that has an Active contract (one, today: the app is single-tenant)."""
    orgs = db.scalars(
        select(Contract.org_id).where(Contract.deleted_at.is_(None), Contract.lifecycle_stage == "active").distinct()
    ).all()
    return [{"org_id": org, **audit_extraction(db, org_id=org, now=now)} for org in orgs]
