"""Produces one Ask Aegis answer, independent of any browser.

``drive_run`` is what the Celery task ``run_assistant_turn`` runs. It drives
the AI controller for a run, publishes every event to the run's Redis stream
(``app.assistant.run_events``) for whoever is watching, and always leaves the
run in a final state with the answer saved — whether the browser stayed, left,
or never existed. Leaving the page is not a cancel; only the Stop button is
(``run_events.request_cancel``), checked between events.

Run states written here:
  succeeded             answer saved
  waiting_confirmation  paused on an action the user must approve (the
                        controller sets this); continued by a resume task
  cancelled             the user pressed Stop; any partial answer is saved
  failed                an error; partial answer saved, generic message shown
  interrupted           the worker stopped unexpectedly (also set by
                        ``sweep_stale_runs`` for runs whose worker died)
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant import run_events
from app.assistant.models import AssistantMessage, AssistantRun, AssistantSession
from app.assistant.service import AssistantService
from app.core.database import utcnow
from app.core.enums import AISkillRunStatus, AssistantRunStatus, UserStatus

logger = logging.getLogger(__name__)

RunMode = Literal["start", "resume"]

# How often the worker checks whether the user pressed Stop (one Redis GET).
CANCEL_CHECK_SECONDS = 0.5
INTERRUPTED_MESSAGE = "This answer didn't finish because Aegis stopped unexpectedly. Please try again."
CANCELLED_MESSAGE = "Stopped before the answer finished."


def user_facing_error(exc: Exception, *, request_id: str | None, run_id: str) -> str:
    """What the user (and the run row, which the runs API returns) may see.

    An HTTPException carries a message written for users (e.g. the AI budget
    guard). Anything else — provider/SDK errors, a missing API key, database
    errors — can expose internals, so it is logged in full with the request id
    and replaced by a generic message carrying that id for support."""
    if isinstance(exc, HTTPException) and isinstance(exc.detail, str):
        return exc.detail
    logger.error("assistant run %s failed (request %s)", run_id, request_id, exc_info=exc)
    ref = f" (reference {request_id})" if request_id else ""
    return f"Aegis couldn't finish this answer. Please try again{ref}."


def citations_from_tool_result(result: dict | None) -> list[dict]:
    if not isinstance(result, dict):
        return []
    citations: list[dict] = []
    if result.get("text_snapshot_id"):
        excerpt = (result.get("text_excerpt") or result.get("text") or "")[:1200]
        citations.append(
            {
                "type": "text_snapshot",
                "contract_id": result.get("contract_id"),
                "text_snapshot_id": result.get("text_snapshot_id"),
                "start_char": 0 if excerpt else None,
                "end_char": len(excerpt) if excerpt else None,
                "excerpt": excerpt,
            }
        )
    for match in result.get("matches") or []:
        citations.append(
            {
                "type": "match",
                "contract_id": result.get("contract_id"),
                "start_char": match.get("start_char"),
                "end_char": match.get("end_char"),
                "excerpt": match.get("excerpt"),
            }
        )
    for cit in result.get("citations") or []:
        if isinstance(cit, dict) and (cit.get("excerpt") or cit.get("quote")):
            citations.append(
                {
                    "type": cit.get("type", "citation"),
                    "contract_id": result.get("contract_id"),
                    "excerpt": cit.get("excerpt") or cit.get("quote"),
                }
            )
    return citations


# Tools whose result is an intake request the user should be able to open. Their
# result carries {id, ref}; we persist those on the tool block so the trace can
# render an "Open REQ-…" link after reload (the live stream is replaced by these
# persisted blocks once the answer lands).
REQUEST_LINK_TOOLS = {
    "create_intake_request",
    "get_intake_request",
    "start_intake_workflow",
    "advance_intake_workflow",
}


def accumulate_block(blocks: list[dict], event: dict) -> None:
    """Build an ordered, persistable timeline of the assistant turn (content
    interleaved with tool steps) so the Mike-style trace survives reload."""
    name = event.get("event")
    payload = event.get("payload") or {}
    if name == "message_delta":
        text = payload.get("text", "")
        if not text:
            return
        if blocks and blocks[-1].get("type") == "content":
            blocks[-1]["text"] += text
        else:
            blocks.append({"type": "content", "text": text})
    elif name == "tool_started":
        blocks.append(
            {
                "type": "tool",
                "name": payload.get("tool_name", "tool"),
                "status": "running",
            }
        )
    elif name == "tool_finished":
        result = payload.get("result")
        for b in reversed(blocks):
            if b.get("type") == "tool" and b.get("status") == "running":
                b["status"] = "error" if payload.get("error") else "done"
                if isinstance(result, dict):
                    art = {
                        k: result.get(k)
                        for k in (
                            "artifact_type",
                            "contract_id",
                            "edits",
                            "summary",
                        )
                        if result.get(k) is not None
                    }
                    if (
                        b.get("name") in REQUEST_LINK_TOOLS
                        and isinstance(result.get("id"), str)
                        and isinstance(result.get("ref"), str)
                    ):
                        art["request_id"] = result["id"]
                        art["request_ref"] = result["ref"]
                    if art:
                        b["artifact"] = art
                break


def events_from_tool_result(result: dict | None) -> list[dict]:
    if not isinstance(result, dict):
        return []
    events = [
        {"event": "citation", "payload": citation}
        for citation in citations_from_tool_result(result)
    ]
    artifact_type = result.get("artifact_type")
    if artifact_type == "generated_contract":
        events.append(
            {
                "event": "contract_generated",
                "payload": {
                    "contract_id": result.get("contract_id"),
                    "contract_file_id": result.get("contract_file_id"),
                    "contract_version_id": result.get("contract_version_id"),
                },
            }
        )
    if artifact_type in {"assistant_edit", "playbook_redline"}:
        events.append(
            {
                "event": "tracked_change_created",
                "payload": {
                    "contract_id": result.get("contract_id"),
                    "base_version_id": result.get("base_version_id"),
                    "contract_version_id": result.get("contract_version_id"),
                    "contract_edit_id": result.get("contract_edit_id"),
                },
            }
        )
    if result.get("playbooks"):
        events.append(
            {
                "event": "playbooks_offered",
                "payload": {"playbooks": result["playbooks"]},
            }
        )
    return events


def _publish_end(run_id: str, status: str, *, error: str | None = None) -> None:
    if error:
        run_events.publish(run_id, "error", {"message": error, "assistant_run_id": run_id})
    run_events.publish(run_id, run_events.TERMINAL_EVENT, {"assistant_run_id": run_id, "run_status": status})


def _close_skill_runs(db: Session, run_id: str) -> None:
    """A Stop ends the controller mid-loop, so its AI skill run never reaches its
    own final state; record it as cancelled rather than leave it 'running'."""
    from app.ai.models import AISkillRun

    for skill_run in db.scalars(
        select(AISkillRun).where(
            AISkillRun.assistant_run_id == run_id, AISkillRun.status == AISkillRunStatus.RUNNING
        )
    ):
        skill_run.status = AISkillRunStatus.CANCELLED
        skill_run.finished_at = utcnow()


async def drive_run(
    db: Session,
    *,
    run_id: str,
    mode: RunMode,
    confirmation_id: str | None = None,
    request_id: str | None = None,
    controller=None,
) -> str:
    """Run the answer for ``run_id`` to a final state; returns that state.

    ``controller`` defaults to the shared ``ai_controller`` (a parameter so
    tests can drive this without a model)."""
    if controller is None:
        from app.ai.controller import ai_controller as controller

    run = db.get(AssistantRun, run_id)
    if run is None:
        logger.warning("assistant run %s not found", run_id)
        return "missing"
    if run.status != AssistantRunStatus.RUNNING:
        # Already finished, stopped or swept: never produce an answer twice.
        logger.info("assistant run %s is %s; nothing to do", run_id, run.status)
        return run.status

    from app.auth.models import User

    session = db.get(AssistantSession, run.session_id)
    user = db.get(User, run.created_by_user_id) if run.created_by_user_id else None
    if session is None or user is None or user.status != UserStatus.ACTIVE:
        run.status = AssistantRunStatus.FAILED
        run.error_message = "This chat can't be answered: its owner no longer has access."
        run.completed_at = utcnow()
        db.commit()
        _publish_end(run_id, run.status, error=run.error_message)
        return run.status

    service = AssistantService(db)
    if mode == "start":
        user_message = db.get(AssistantMessage, run.user_message_id) if run.user_message_id else None
        contract_ids = list((run.context_manifest or {}).get("contract_ids") or [])
        events = controller.stream_assistant_run(
            db,
            user=user,
            org_id=run.org_id,
            created_by_user_id=user.id,
            session_id=session.id,
            assistant_run_id=run.id,
            message=user_message.content if user_message else "",
            request_id=request_id,
            contract_id=session.contract_id or (contract_ids[0] if contract_ids else None),
            contract_ids=contract_ids,
            session_type=session.session_type,
        )
    else:
        events = controller.resume_assistant_run(
            db,
            user=user,
            assistant_run_id=run.id,
            confirmation_id=confirmation_id,
            request_id=request_id,
        )
    extra = {"resumed": True} if mode == "resume" else {}

    answer_parts: list[str] = []
    citations: list[dict] = []
    blocks: list[dict] = []
    waiting_for_confirmation = False
    cancelled = False
    finalized = False
    last_cancel_check = 0.0  # so the first event already checks for a Stop

    def persist(**more) -> None:
        service.persist_assistant_answer(
            org_id=run.org_id,
            session_id=session.id,
            run=run,
            answer_parts=answer_parts,
            citations=citations,
            blocks=blocks,
            current_user=user,
            extra_metadata={**extra, **more} or None,
        )

    try:
        async for event in events:
            # Checked before using the event, so a Stop pressed while the model
            # was still thinking keeps nothing of what arrives after it. Never on
            # a confirmation request: the controller has already paused the run
            # for it, and that card must stay answerable.
            now = time.monotonic()
            if (
                event["event"] != "confirmation_required"
                and now - last_cancel_check >= CANCEL_CHECK_SECONDS
            ):
                last_cancel_check = now
                if run_events.cancel_requested(run_id):
                    cancelled = True
                    break
            name = event["event"]
            payload = event["payload"]
            if name == "message_delta":
                answer_parts.append(payload.get("text", ""))
            elif name == "confirmation_required":
                waiting_for_confirmation = True
            elif name == "tool_finished":
                citations.extend(citations_from_tool_result(payload.get("result")))
            accumulate_block(blocks, event)
            run_events.publish(run_id, name, payload)
            if name == "tool_finished":
                for extra_event in events_from_tool_result(payload.get("result")):
                    run_events.publish(run_id, extra_event["event"], extra_event["payload"])
        if cancelled:
            await events.aclose()
            db.rollback()
            db.refresh(run)
            _close_skill_runs(db, run_id)
            persist(cancelled=True)
            run.status = AssistantRunStatus.CANCELLED
            run.error_message = CANCELLED_MESSAGE
            run.completed_at = utcnow()
            db.commit()
            finalized = True
            _publish_end(run_id, run.status)
            return run.status
        if waiting_for_confirmation:
            # The controller already set WAITING_CONFIRMATION and committed.
            db.refresh(run)
            finalized = True
            _publish_end(run_id, run.status)
            return run.status
        persist()
        run.status = AssistantRunStatus.SUCCEEDED
        run.completed_at = utcnow()
        db.commit()
        finalized = True
        _publish_end(run_id, run.status)
        return run.status
    except Exception as exc:
        db.rollback()
        try:
            persist(interrupted=True)
        except Exception:
            db.rollback()
        message = user_facing_error(exc, request_id=request_id, run_id=run_id)
        run.status = AssistantRunStatus.FAILED
        run.error_message = message
        run.completed_at = utcnow()
        db.commit()
        finalized = True
        _publish_end(run_id, run.status, error=message)
        return run.status
    finally:
        if not finalized:
            # Cancelled from outside (worker shutdown, hard time limit): never
            # leave the run "running" with no explanation.
            try:
                db.rollback()
                current = db.get(AssistantRun, run_id)
                if current is not None and current.status == AssistantRunStatus.RUNNING:
                    current.status = AssistantRunStatus.INTERRUPTED
                    current.error_message = INTERRUPTED_MESSAGE
                    current.completed_at = utcnow()
                    db.commit()
                    _publish_end(run_id, current.status, error=INTERRUPTED_MESSAGE)
            except Exception:
                db.rollback()


def run_lease() -> timedelta:
    """A worker can't hold a run past Celery's hard time limit (it is killed),
    so a run still 'running' after that plus a margin lost its worker."""
    from app.jobs.celery_app import celery_app

    return timedelta(seconds=(celery_app.conf.task_time_limit or 900) + 300)


def sweep_stale_runs(db: Session, *, now=None, limit: int = 200) -> int:
    """Mark runs whose worker died as interrupted and tell any watcher, so the
    chat shows 'didn't finish — Retry' instead of 'Thinking…' forever."""
    now = now or utcnow()
    stale = db.scalars(
        select(AssistantRun)
        .where(
            AssistantRun.status == AssistantRunStatus.RUNNING,
            AssistantRun.updated_at < now - run_lease(),
        )
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for run in stale:
        run.status = AssistantRunStatus.INTERRUPTED
        run.error_message = INTERRUPTED_MESSAGE
        run.completed_at = now
    db.commit()
    for run in stale:
        _publish_end(run.id, run.status, error=INTERRUPTED_MESSAGE)
        run_events.release_claim(run.id)
    return len(stale)
