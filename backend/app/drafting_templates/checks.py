"""Checking a drafting template against its pinned playbook: queueing, running and judging.

A check is one AI review of a template's wording, the review a contract drafted from it would
get (``PlaybooksService.review_text``). It is recorded (``DraftingTemplateCheck``), runs in the
background as the ``template_check`` job, and never creates a contract, run or finding.

Single flight (FR-15). ``request_check`` takes a per-template advisory lock and under it keeps at
most one running and one queued check per template; two partial unique indexes make that a
database fact too. Lock order: a mutation commits (with its audit row) BEFORE it calls
``request_check``, which takes the template lock and then the audit lock. The worker writes its
audit rows without the template lock, and ``_start_next`` takes the template lock with no audit,
so no transaction holds the audit lock while waiting for a template lock (CLAUDE.md, audit-lock
freeze). The audit chain's key is a bigint; ``hashtext`` is an int4, so the two never collide.

No automatic retry anywhere (FR-20): the handler swallows every failure into the check's own
record, so Celery's ``autoretry_for`` never fires; a reclaimed job (``attempt_count > 1``) fails
as ``interrupted`` without calling the AI; a FAILED job is never re-dispatched. Because the job is
the unit of at-least-once delivery, ``interrupted`` is the price of never paying twice.
ponytail: a check whose worker dies three times is dead-lettered by reclaim and stays
``running``; the upgrade path is a reclaim hook that fails the check.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import User
from app.core.audit import write_audit_log
from app.core.database import new_uuid, utcnow
from app.core.enums import JobStatus
from app.drafting_templates.models import DraftingTemplateCheck, DraftingTemplateVersion
from app.drafting_templates.service import (
    SAMPLE_VALUES,
    Reviewing,
    TemplateType,
    Wording,
    body_sha256,
    check_body,
    current_wording,
    default_body,
    ensure_pinned,
    get_type,
    list_types,
    reviewing_playbook,
)
from app.jobs.models import JobRun
from app.jobs.service import create_job, dispatch_job

logger = logging.getLogger(__name__)

JOB_TYPE = "template_check"
RESOURCE_TYPE = "drafting_template_check"

# Triggers that are a person's own action. Every other trigger is the system acting on behalf of
# the person whose change caused it (a save, a publish, a pin change): it is attributed that way
# in the audit trail and skips a retired type.
_MANUAL = frozenset({"manual", "retry", "check_all"})

LABELS = {
    "passes": "Passes",
    "has_findings": "Has findings",
    "checking": "Checking",
    "out_of_date": "Out of date",
    "partly_checked": "Partly checked",
    "couldnt_check": "Couldn't check",
    "never_checked": "Never checked",
    "no_playbook": "No playbook",
}
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

QUEUE_FULL = "A check of this template is already waiting to start. Try again once it has started."
_MSG_AI = "The AI review could not run, so the template was not checked. Try again in a moment."
_MSG_CAP = ("The organisation's daily AI spending limit has been reached, so the template was not "
            "checked. Try again tomorrow.")
_MSG_CHANGED = ("The template's wording changed while this check was waiting, so it could not read the "
                "wording it was asked to check. Try again.")
_MSG_INTERRUPTED = ("This check was interrupted before it finished. It is not run again on its own, "
                    "because every review is paid for. Try again.")


class _Failed(Exception):
    """A check that cannot complete, with the code and the plain words the page shows."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --- Locks, audit, dispatch --------------------------------------------------------------------


def _lock(db: Session, org_id: str, key: str) -> None:
    """The template's single-flight lock, held until the transaction ends (commit)."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"drafting_template_check:{org_id}:{key}"))))


def _audit(db: Session, action: str, check: DraftingTemplateCheck, after: dict[str, Any], *,
           person: str | None, on_behalf_of: str | None) -> None:
    """A check event. A person's own action names them; the system's names who it acted for."""
    write_audit_log(
        db, action=f"drafting_template.{action}", resource_type=RESOURCE_TYPE, resource_id=check.id,
        org_id=check.org_id, actor_user_id=person, after=after,
        metadata=None if person else {"actor": "system", "on_behalf_of_user_id": on_behalf_of},
    )


def _dispatch(db: Session, check: DraftingTemplateCheck) -> None:
    """Hand a queued check to the worker. The job row is committed before the broker is asked, and
    a broker failure leaves it queued: the periodic reclaim dispatches any queued job that never
    got a task id, so a hiccup delays the check and does not lose it."""
    try:
        job = create_job(
            db, org_id=check.org_id, job_type=JOB_TYPE, resource_type=RESOURCE_TYPE, resource_id=check.id,
            created_by_user_id=check.created_by_user_id, idempotency_key=f"{JOB_TYPE}:{check.id}",
            metadata={"template_key": check.template_key},
        )
        db.commit()
        dispatch_job(db, job=job)
        db.commit()
    except Exception:
        logger.warning("could not dispatch template check %s; it stays queued", check.id, exc_info=True)
        if not db.is_active:
            db.rollback()


# --- Requesting a check ------------------------------------------------------------------------


def _in_flight(db: Session, org_id: str, key: str) -> tuple[DraftingTemplateCheck | None, DraftingTemplateCheck | None]:
    rows = db.scalars(
        select(DraftingTemplateCheck)
        .where(DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key,
               DraftingTemplateCheck.status.in_(("queued", "running")))
        .execution_options(populate_existing=True)
    ).all()
    return (next((c for c in rows if c.status == "queued"), None),
            next((c for c in rows if c.status == "running"), None))


def _same_wording(check: DraftingTemplateCheck, wording: Wording) -> bool:
    return (not check.unsaved and check.template_version == wording.version
            and check.wording_sha256 == wording.sha256)


def _latest_finished(db: Session, org_id: str, key: str) -> DraftingTemplateCheck | None:
    """The newest finished check of the saved wording, completed or failed."""
    return db.scalar(
        select(DraftingTemplateCheck)
        .where(DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key,
               DraftingTemplateCheck.unsaved.is_(False),
               DraftingTemplateCheck.status.in_(("completed", "failed")))
        .order_by(DraftingTemplateCheck.finished_at.desc().nulls_last(), DraftingTemplateCheck.created_at.desc())
        .limit(1)
    )


def request_check(db: Session, *, org_id: str, key: str, trigger: str, actor_user_id: str | None,
                  unsaved_body: str | None = None,
                  trigger_detail: dict | None = None) -> DraftingTemplateCheck | None:
    """Queue a check of this template and commit. None when no playbook covers it (FR-9): no row,
    no job, no AI cost. Raises 404 (unknown key), 422 (unsaved wording drafting couldn't fill) and
    409 (an unsaved check while one is already waiting). The rules, in order, under the template
    lock:

    1. an automatic trigger skips a retired type; the type is pinned to its playbook (D-11) and a
       missing or unavailable playbook ends the request;
    2. an unsaved request is refused (409) while a check is waiting, otherwise queued;
    3. a waiting check of the same saved wording is returned as is (it resolves the playbook version
       when it runs, so it already covers a newer publication); a running check of the same wording
       is returned for a person's own request only;
    4. otherwise a waiting check is superseded and the new one queued; it is dispatched at once
       when nothing is running, else when the running check finishes (``_start_next``).

    A person's ``manual`` request is recorded as ``retry`` when the newest finished check of this
    exact wording failed (FR-8). An automatic trigger is the system acting on behalf of
    ``actor_user_id`` (audit actor None); a manual one is the person's own."""
    if unsaved_body is not None:
        check_body(unsaved_body)  # refuses, naming the line, before any lock, row or cost
    get_type(db, org_id, key)  # 404 before a lock is taken for a name the caller made up
    by_person = trigger in _MANUAL
    _lock(db, org_id, key)
    ttype = get_type(db, org_id, key)
    if not by_person and not ttype.active:
        db.commit()
        return None
    ensure_pinned(db, org_id=org_id, ttype=ttype, actor_user_id=actor_user_id, commit=False)
    ttype = get_type(db, org_id, key)  # sees the pin just written
    reviewing = reviewing_playbook(db, org_id, ttype)
    if not reviewing.available:
        db.commit()
        return None

    wording = current_wording(db, org_id, ttype)
    queued, running = _in_flight(db, org_id, key)
    if unsaved_body is not None:
        if queued is not None:
            db.commit()
            raise HTTPException(http_status.HTTP_409_CONFLICT, QUEUE_FULL)
        version, sha = wording.version, body_sha256(unsaved_body)
    else:
        if queued is not None and _same_wording(queued, wording):
            db.commit()
            if running is None:
                _dispatch(db, queued)  # harmless when it already has its task
            return queued
        if running is not None and by_person and _same_wording(running, wording):
            db.commit()
            return running
        version, sha = wording.version, wording.sha256
        if trigger == "manual" and _failed_on(db, org_id, key, wording, reviewing):
            trigger = "retry"

    new_id = new_uuid()
    person = actor_user_id if by_person else None
    if queued is not None:
        queued.status = "superseded"
        queued.finished_at = utcnow()
        queued.updated_by_user_id = actor_user_id
        db.flush()  # frees the queued slot before the new row takes it
        _audit(db, "check_superseded", queued, {"check_id": queued.id, "superseded_by_check_id": new_id},
               person=person, on_behalf_of=actor_user_id)
    check = DraftingTemplateCheck(
        id=new_id, org_id=org_id, template_key=key, template_version=version, wording_sha256=sha,
        unsaved=unsaved_body is not None, unsaved_body=unsaved_body, trigger=trigger,
        trigger_detail=trigger_detail, status="queued", created_by_user_id=actor_user_id,
        updated_by_user_id=actor_user_id,
    )
    db.add(check)
    db.flush()
    _audit(db, "check_started", check,
           {"check_id": check.id, "key": key, "template_version": version, "trigger": trigger,
            "unsaved": check.unsaved, "on_behalf": not by_person},
           person=person, on_behalf_of=actor_user_id)
    db.commit()
    if running is None:
        _dispatch(db, check)
    return check


def _failed_on(db: Session, org_id: str, key: str, wording: Wording, reviewing: Reviewing) -> bool:
    """Whether the newest finished saved check failed on exactly what a new check would read, which
    makes a person's request a retry rather than a fresh look."""
    last = _latest_finished(db, org_id, key)
    if last is None or last.status != "failed" or not _same_wording(last, wording):
        return False
    version = reviewing.version
    return last.playbook_version_id is None or (version is not None and last.playbook_version_id == version.id)


def _start_next(db: Session, org_id: str, key: str) -> None:
    """After a check finishes: dispatch the oldest waiting one, if nothing is running."""
    _lock(db, org_id, key)
    running = db.scalar(
        select(func.count()).select_from(DraftingTemplateCheck)
        .where(DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key,
               DraftingTemplateCheck.status == "running")
    )
    waiting = None if running else db.scalar(
        select(DraftingTemplateCheck)
        .where(DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key,
               DraftingTemplateCheck.status == "queued")
        .order_by(DraftingTemplateCheck.created_at.asc())
        .limit(1)
    )
    db.commit()
    if waiting is not None:
        _dispatch(db, waiting)


# --- Reading checks back -----------------------------------------------------------------------


def list_checks(db: Session, *, org_id: str, key: str, limit: int = 50) -> list[dict]:
    """The checks of this template, newest first, unsaved ones included (they say so). 404 for a
    template that is not this organisation's."""
    get_type(db, org_id, key)
    rows = db.scalars(
        select(DraftingTemplateCheck)
        .where(DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key)
        .order_by(DraftingTemplateCheck.created_at.desc(), DraftingTemplateCheck.id)
        .limit(limit)
    ).all()
    return [serialize_check(db, c, full=True) for c in rows]


def get_check(db: Session, *, org_id: str, key: str, check_id: str) -> dict:
    """One check of this template. 404 when it is another organisation's or another template's."""
    get_type(db, org_id, key)
    check = db.scalar(
        select(DraftingTemplateCheck).where(
            DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key,
            DraftingTemplateCheck.id == check_id)
    )
    if check is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "No such check.")
    return serialize_check(db, check, full=True)


# --- Check all and publish ---------------------------------------------------------------------


def _has_active_saved_check(db: Session, org_id: str, key: str) -> bool:
    return db.scalar(
        select(func.count()).select_from(DraftingTemplateCheck)
        .where(DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key,
               DraftingTemplateCheck.unsaved.is_(False),
               DraftingTemplateCheck.status.in_(("queued", "running")))
    ) > 0


def check_all(db: Session, *, user: User) -> dict:
    """One check per active template (FR-17), each through ``request_check``. A template already
    being checked, or with no playbook, is skipped; one that fails to queue is logged and the rest
    carry on."""
    started: list[dict] = []
    skipped: list[dict] = []
    for ttype in list_types(db, user.org_id):
        if not reviewing_playbook(db, user.org_id, ttype).available:
            skipped.append({"key": ttype.key, "reason": "no_playbook"})
        elif _has_active_saved_check(db, user.org_id, ttype.key):
            skipped.append({"key": ttype.key, "reason": "checking"})
        else:
            try:
                check = request_check(db, org_id=user.org_id, key=ttype.key, trigger="check_all",
                                      actor_user_id=user.id)
            except Exception:
                logger.exception("check all could not queue the check of %s", ttype.key)
                if not db.is_active:
                    db.rollback()
                continue
            if check is None:
                skipped.append({"key": ttype.key, "reason": "no_playbook"})
            else:
                started.append({"key": ttype.key, "check_id": check.id})
    write_audit_log(
        db, action="drafting_template.check_all_started", resource_type="drafting_template",
        org_id=user.org_id, actor_user_id=user.id,
        after={"count": len(started), "keys": [s["key"] for s in started]},
    )
    db.commit()
    return {"started": started, "skipped": skipped}


def on_playbooks_published(db: Session, *, org_id: str, playbook_ids: Sequence[str], actor_user_id: str | None,
                           detail_for: Callable[[str], dict]) -> None:
    """After a playbook publishes (FR-14): check every template that playbook reviews. That is a
    template pinned to it, or an unpinned shipped template whose name match it now is (the check
    pins it). A template pinned to another playbook is not touched, however well this one is named
    (D-11). One commit per template; nothing here ever fails the publish, which has already
    committed."""
    wanted = set(playbook_ids)
    try:
        types = list_types(db, org_id)
    except Exception:
        logger.exception("could not list the templates after a publish")
        return
    for ttype in types:
        try:
            playbook = reviewing_playbook(db, org_id, ttype).playbook
            if playbook is None or playbook.id not in wanted:
                continue
            request_check(db, org_id=org_id, key=ttype.key, trigger="playbook_published",
                          actor_user_id=actor_user_id, trigger_detail=detail_for(playbook.id))
        except Exception:
            logger.exception("could not check template %s after a publish", ttype.key)
            if not db.is_active:
                db.rollback()


# --- Running a check ---------------------------------------------------------------------------


def check_values(key: str) -> dict[str, str]:
    """What the check fills the template with: a request with no form answers."""
    from app.intake.drafting import template_values as fill_values

    return fill_values(key, company=SAMPLE_VALUES["company"], counterparty=SAMPLE_VALUES["counterparty"],
                       effective=SAMPLE_VALUES["effective_date"], fields={})


_CODED = ("company", "counterparty", "effective_date", "governing_law", "agreement_term", "purpose_phrase",
          "direction_recital", "term_clause", "survival_clause")
_MIN_CODED_LENGTH = 12  # a shorter value ("Mutual", "45") is as likely to be ordinary wording as a field


def template_form_of(text: str, values: dict[str, str]) -> str:
    """The filled text turned back into template wording: literal braces doubled, and each long
    value replaced by its ``{code}``. The page can then find a quoted passage in the template, and
    Apply never writes the sample party names into our own paper."""
    out = text.replace("{", "{{").replace("}", "}}")
    named = sorted(((n, values[n]) for n in _CODED if len(values.get(n) or "") >= _MIN_CODED_LENGTH),
                   key=lambda pair: len(pair[1]), reverse=True)
    for name, value in named:
        out = out.replace(value, "{" + name + "}")
    return out


def _enrich(findings: list[dict], values: dict[str, str]) -> list[dict]:
    from app.playbooks.service import is_document_wording

    out = []
    for index, f in enumerate(findings):
        quote, fix = f.get("quote"), f.get("suggested_fix")
        severity = f["severity"]
        out.append({
            "index": index, "rule_index": f.get("rule_index"), "clause_type": f["clause_type"],
            "severity": str(getattr(severity, "value", severity)), "issue": f["issue"],
            "quote": quote, "quote_template": template_form_of(quote, values) if quote else None,
            "suggested_fix": fix,
            "suggested_fix_template": template_form_of(fix, values) if fix else None,
            "suggestion_is_wording": is_document_wording(fix),
            "approval_required": bool(f.get("approval_required")),
        })
    return out


def _no_playbook_reason(ttype: TemplateType, reviewing: Reviewing) -> str:
    """Why nothing reviews this template, in the words the page and the 409 use."""
    if not reviewing.pinned:
        return f"No published playbook covers {ttype.contract_type} contracts. Choose a playbook for this template."
    playbook = reviewing.playbook
    who = playbook.name if playbook is not None else "its playbook"
    why = "was deleted" if playbook is None or playbook.deleted_at is not None else "has no published version"
    return (f"{ttype.name} is reviewed by {who}, which {why}. Publish it, or choose another playbook "
            "for this template.")


def no_playbook_detail(db: Session, *, org_id: str, ttype: TemplateType) -> str | None:
    """The 409 for a person's own check when nothing can review this template, or None when
    something can. (An unpinned type with a name match is checkable: the request pins it.)"""
    reviewing = reviewing_playbook(db, org_id, ttype)
    if reviewing.available:
        return None
    if not reviewing.pinned:
        return f"No published playbook covers {ttype.name}, so there is nothing to check it against."
    return _no_playbook_reason(ttype, reviewing)


def _wording_read(db: Session, check: DraftingTemplateCheck) -> str:
    """The wording this check was asked to read, or ``_Failed`` when it is no longer what it was
    queued for."""
    if check.unsaved:
        return check.unsaved_body or ""
    if check.template_version == 0:
        body = default_body(check.template_key)
    else:
        row = db.scalar(
            select(DraftingTemplateVersion).where(
                DraftingTemplateVersion.org_id == check.org_id, DraftingTemplateVersion.key == check.template_key,
                DraftingTemplateVersion.version == check.template_version)
        )
        if row is None:
            raise _Failed("wording_changed", _MSG_CHANGED)
        body = row.body if row.body is not None else default_body(check.template_key)
    if body_sha256(body) != check.wording_sha256:
        raise _Failed("wording_changed", _MSG_CHANGED)
    return body


def _asker(db: Session, check: DraftingTemplateCheck) -> User:
    """Whose name the review runs under: the person the check is for, else the org's oldest user."""
    user = db.get(User, check.created_by_user_id) if check.created_by_user_id else None
    if user is None or user.org_id != check.org_id:
        user = db.scalar(select(User).where(User.org_id == check.org_id).order_by(User.created_at.asc()))
    if user is None:
        raise _Failed("ai_unavailable", _MSG_AI)
    return user


async def _review(db: Session, check: DraftingTemplateCheck) -> dict:
    """Do the check and return the fields it records. Raises ``_Failed`` for every outcome the page
    has words for. The worker reads the template's PINNED playbook only: it never pins and never
    falls back to a name match, so a better-named playbook can't take a template over (D-11)."""
    from app.ai import context as ai_context
    from app.intake.drafting import render_document
    from app.playbooks.models import PlaybookVersion
    from app.playbooks.service import PlaybooksService, rules_in_review_order

    org_id, key = check.org_id, check.template_key
    ttype = get_type(db, org_id, key)
    # reviewing_playbook would offer an unpinned type its name match; the worker takes the pin or nothing.
    reviewing = reviewing_playbook(db, org_id, ttype) if ttype.playbook_id else Reviewing(None, False, False)
    if not reviewing.available or reviewing.playbook is None:
        raise _Failed("no_playbook", _no_playbook_reason(ttype, reviewing))
    playbook = reviewing.playbook
    check.playbook_id, check.playbook_name = playbook.id, playbook.name

    playbooks = PlaybooksService(db)
    try:
        version = playbooks.select_run_version(playbook=playbook, org_id=org_id, version_id=None, test_mode=False)
    except HTTPException:
        raise _Failed("no_playbook", _no_playbook_reason(ttype, Reviewing(playbook, True, False))) from None
    check.playbook_version_id, check.playbook_version_number = version.id, version.version_number
    rules = rules_in_review_order(db, org_id=org_id, playbook_version_id=version.id)
    if not rules:
        raise _Failed("no_rules", f"{playbook.name} (version {version.version_number}) has no rules, so "
                                  "there is nothing to check the template against.")
    check.rule_count = len(rules)

    body = _wording_read(db, check)
    try:
        check_body(body)
    except HTTPException as exc:
        raise _Failed("unfillable", str(exc.detail)) from None
    text = render_document(key, company=SAMPLE_VALUES["company"], counterparty=SAMPLE_VALUES["counterparty"],
                           effective=SAMPLE_VALUES["effective_date"], fields={}, body=body)

    user = _asker(db, check)
    try:
        result = await playbooks.review_text(
            user=user, playbook=playbook, text=text, title=ttype.name, contract_type=ttype.contract_type,
            counterparty_name=SAMPLE_VALUES["counterparty"], resource_type=RESOURCE_TYPE, resource_id=check.id,
        )
    except HTTPException as exc:
        if exc.status_code == http_status.HTTP_429_TOO_MANY_REQUESTS:
            raise _Failed("cost_cap", _MSG_CAP) from None
        logger.warning("template check %s: the review was refused (%s)", check.id, exc.status_code)
        raise _Failed("ai_unavailable", _MSG_AI) from None
    except Exception:
        logger.exception("template check %s: the AI review failed", check.id)
        raise _Failed("ai_unavailable", _MSG_AI) from None

    used = db.get(PlaybookVersion, result["playbook_version_id"])
    return {
        "playbook_id": result["playbook_id"], "playbook_name": result["playbook_name"],
        "playbook_version_id": result["playbook_version_id"],
        "playbook_version_number": used.version_number if used is not None else version.version_number,
        "rule_count": result["rule_count"],
        "findings": _enrich(result["findings"], check_values(key)),
        "summary": result["summary"], "text_was_truncated": bool(result["text_was_truncated"]),
        "text_length": len(text), "read_length": min(len(text), ai_context.MAX_FULL_TEXT_CHARS),
    }


def _end_job(job: JobRun, *, ok: bool, error: str | None = None) -> None:
    job.status = JobStatus.SUCCEEDED if ok else JobStatus.FAILED
    job.progress = 100
    job.finished_at = utcnow()
    job.error_message = error
    job.error_stack = None


def _fail(db: Session, check: DraftingTemplateCheck, job: JobRun, code: str, message: str) -> None:
    check.status, check.error_code, check.error_message = "failed", code, message
    check.finished_at = utcnow()
    _audit(db, "check_failed", check,
           {"check_id": check.id, "key": check.template_key, "template_version": check.template_version,
            "playbook_version_id": check.playbook_version_id, "trigger": check.trigger,
            "error_code": code, "error_message": message},
           person=None, on_behalf_of=check.created_by_user_id)
    _end_job(job, ok=False, error=message)
    db.commit()


async def run_check_job(db: Session, job: JobRun) -> None:
    """The ``template_check`` job. Each step commits on its own and no audit row is held open
    across the AI call. Nothing is re-raised: every outcome is recorded on the check and the job."""
    check = db.scalar(
        select(DraftingTemplateCheck)
        .where(DraftingTemplateCheck.id == job.resource_id, DraftingTemplateCheck.org_id == job.org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if check is None:
        _end_job(job, ok=False, error="The check was not found.")
        db.commit()
        return
    org_id, key = check.org_id, check.template_key
    if job.attempt_count > 1 and check.status in ("queued", "running"):
        # A dead worker's job handed to a new one. The first attempt may already have been paid
        # for, so this one is not run again (FR-20): it ends as interrupted (a check the dead worker
        # had already set running is also left running, which this resolves).
        _fail(db, check, job, "interrupted", _MSG_INTERRUPTED)
        _start_next(db, org_id, key)
        return
    if check.status != "queued":
        _end_job(job, ok=True)  # superseded while waiting, or already handled
        db.commit()
        return
    check.status, check.started_at = "running", utcnow()
    db.commit()

    done: dict | None = None
    failure: _Failed | None = None
    try:
        done = await _review(db, check)
    except _Failed as exc:
        failure = exc
    except Exception:
        logger.exception("template check %s failed unexpectedly", check.id)
        failure = _Failed("ai_unavailable", _MSG_AI)
    if not db.is_active:
        db.rollback()  # a failed flush inside the review: nothing of it is wanted
        check = db.get(DraftingTemplateCheck, job.resource_id)

    if failure is not None:
        _fail(db, check, job, failure.code, failure.message)
    else:
        for field, value in done.items():
            setattr(check, field, value)
        check.status, check.finished_at = "completed", utcnow()
        _audit(db, "check_finished", check,
               {"check_id": check.id, "key": key, "template_version": check.template_version,
                "playbook_id": check.playbook_id, "playbook_version_id": check.playbook_version_id,
                "playbook_version_number": check.playbook_version_number, "trigger": check.trigger,
                "finding_count": len(check.findings or []), "text_was_truncated": check.text_was_truncated},
               person=None, on_behalf_of=check.created_by_user_id)
        _end_job(job, ok=True)
        db.commit()
    _start_next(db, org_id, key)


# --- Reading a check back ----------------------------------------------------------------------


def _highest(findings: list[dict] | None) -> str | None:
    if not findings:
        return None
    return max((f["severity"] for f in findings), key=lambda s: _SEVERITY_RANK.get(s, -1))


def _iso(moment) -> str | None:
    return moment.isoformat() if moment is not None else None


def serialize_check(db: Session, check: DraftingTemplateCheck, *, full: bool = False) -> dict:
    """``TemplateCheckSummary``, or ``TemplateCheck`` with ``full``."""
    findings = check.findings
    person = db.get(User, check.created_by_user_id) if check.created_by_user_id else None
    out: dict[str, Any] = {
        "id": check.id, "state": check.status, "trigger": check.trigger, "unsaved": bool(check.unsaved),
        "template_version": check.template_version, "playbook_name": check.playbook_name,
        "playbook_version_number": check.playbook_version_number,
        "finding_count": None if findings is None else len(findings),
        "highest_severity": _highest(findings), "text_was_truncated": check.text_was_truncated,
        "error_code": check.error_code, "error_message": check.error_message,
        "requested_by": (person.full_name or person.email) if person is not None else None,
        "on_behalf": check.trigger not in _MANUAL,
        "created_at": _iso(check.created_at), "started_at": _iso(check.started_at),
        "finished_at": _iso(check.finished_at),
    }
    if full:
        out.update({
            "template_key": check.template_key, "trigger_detail": check.trigger_detail,
            "wording_sha256": check.wording_sha256, "playbook_id": check.playbook_id,
            "playbook_version_id": check.playbook_version_id, "rule_count": check.rule_count,
            "findings": findings or [], "summary": check.summary, "text_length": check.text_length,
            "read_length": check.read_length,
        })
    return out


def _state(db: Session, state: str, reason: str, *, count: int | None = None, severity: str | None = None,
           changed: str | None = None, error_code: str | None = None, vary: bool = False,
           decided: DraftingTemplateCheck | None = None, last: DraftingTemplateCheck | None = None,
           active: DraftingTemplateCheck | None = None) -> dict:
    def summary(c):
        return serialize_check(db, c, full=False) if c is not None else None

    return {
        "state": state, "label": LABELS[state], "reason": reason, "finding_count": count,
        "highest_severity": severity, "changed": changed, "error_code": error_code, "results_vary": vary,
        "decided_by": summary(decided), "last_completed": summary(last), "active_check": summary(active),
    }


def _results_vary(db: Session, org_id: str, key: str, wording: Wording, playbook_version_id: str) -> bool:
    """Whether completed checks of this exact wording and playbook version disagree on there being
    findings (FR-11). Stored records only; it never starts a review. Unsaved checks don't count."""
    rows = db.scalars(
        select(DraftingTemplateCheck.findings)
        .where(DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key,
               DraftingTemplateCheck.unsaved.is_(False), DraftingTemplateCheck.status == "completed",
               DraftingTemplateCheck.template_version == wording.version,
               DraftingTemplateCheck.wording_sha256 == wording.sha256,
               DraftingTemplateCheck.playbook_version_id == playbook_version_id)
    ).all()
    return {bool(f) for f in rows} == {True, False}


def _what_was_not_read(check: DraftingTemplateCheck) -> str:
    total, read = check.text_length, check.read_length
    if not total or read is None or read >= total:
        return "The review did not read the whole template."
    pct = max(1, round((total - read) * 100 / total))
    return f"The review read the first {read:,} of {total:,} characters; the last {pct}% was not checked."


def template_status(db: Session, *, org_id: str, ttype: TemplateType) -> dict:
    """The one status of a template (FR-2), from its stored checks of the SAVED wording. The first
    row of this table that holds is the answer; nothing here starts a check."""
    key = ttype.key
    saved = (DraftingTemplateCheck.org_id == org_id, DraftingTemplateCheck.template_key == key,
             DraftingTemplateCheck.unsaved.is_(False))
    last = db.scalar(
        select(DraftingTemplateCheck).where(*saved, DraftingTemplateCheck.status == "completed")
        .order_by(DraftingTemplateCheck.finished_at.desc().nulls_last(), DraftingTemplateCheck.created_at.desc())
        .limit(1)
    )
    reviewing = reviewing_playbook(db, org_id, ttype)
    if reviewing.playbook is None or not reviewing.available:  # 1
        return _state(db, "no_playbook", _no_playbook_reason(ttype, reviewing), last=last)
    active = db.scalar(
        select(DraftingTemplateCheck).where(*saved, DraftingTemplateCheck.status.in_(("queued", "running")))
        .order_by(DraftingTemplateCheck.created_at.desc()).limit(1)
    )
    pv = reviewing.version
    wording = current_wording(db, org_id, ttype)
    vary = _results_vary(db, org_id, key, wording, pv.id)
    if active is not None:  # 2
        what = "running" if active.status == "running" else "waiting to start"
        return _state(db, "checking", f"A check is {what}.", vary=vary, last=last, active=active)
    a = _latest_finished(db, org_id, key)
    if a is None:  # 3
        return _state(db, "never_checked", "Not checked yet.", vary=vary)
    wording_moved = a.template_version != wording.version or a.wording_sha256 != wording.sha256
    playbook_moved = a.playbook_version_id is not None and a.playbook_version_id != pv.id
    if wording_moved or playbook_moved:  # 4
        changed = "both" if wording_moved and playbook_moved else "wording" if wording_moved else "playbook"
        reason = {
            "wording": "The wording changed after the last check.",
            "playbook": "The playbook changed after the last check.",
            "both": "The wording and the playbook both changed after the last check.",
        }[changed]
        return _state(db, "out_of_date", reason, changed=changed, vary=vary, decided=a, last=last)
    if a.status == "failed":  # 5
        return _state(db, "couldnt_check", a.error_message or "The check could not run.",
                      error_code=a.error_code, vary=vary, decided=a, last=last)
    count, highest = len(a.findings or []), _highest(a.findings)
    if a.text_was_truncated:  # 6
        return _state(db, "partly_checked", _what_was_not_read(a), count=count, severity=highest,
                      vary=vary, decided=a, last=last)
    if count:  # 7: a finding at the fallback position, or of low severity, still counts (FR-4)
        reason = f"{count} finding{'s' if count != 1 else ''}, highest {highest.capitalize()}."
        return _state(db, "has_findings", reason, count=count, severity=highest, vary=vary, decided=a, last=last)
    return _state(  # 8
        db, "passes",
        f"The latest review found nothing in the current wording (against {a.playbook_name} "
        f"version {a.playbook_version_number}).",
        count=0, vary=vary, decided=a, last=last,
    )


def status_notice(status: dict) -> str | None:
    """The notice a draft carries when its template is not Passes (FR-47); None when it is."""
    if status["state"] == "passes":
        return None
    return (f"This template does not currently pass its playbook: {status['label']} — {status['reason']} "
            "The draft was created anyway.")
