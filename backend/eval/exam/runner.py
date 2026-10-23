"""The exam's paid-run machinery and the exam run itself (specs/005-ai-eval-baseline/plan.md,
"Component design" -> ``runner.py``).

Part 1 is everything a paid command shares (``exam``, ``ocr``, ``prefill``, ``draft-routing``):
``Estimate`` and its plain-language dialogue, ``Meter`` (the one object installed as
``app.integrations.claude.exam_meter``), the interactive ``approve_paid_run`` / ``finish_paid_run``
pair that writes the audit trail, and the copy of the exam's AI call records into the live ledger.

Part 2 is the run: ``execute_exam_run`` / ``resume_exam_run`` / ``abandon_exam_run``. It drives the
app's own code (the upload pipeline's read and persist steps, the job handlers' skills, playbook
review, Contract Brain ``ask``, intake triage) against the throwaway exam database, in two
dependency phases. Public and constructed units go through the provider's discounted Message
Batches: the app code is run once to RECORD each request at the Claude boundary (capture), the
requests go out as one batch per phase, and the app code is run again to hand each request its
stored answer (replay) -- so the batch carries exactly the prompts the app builds, with no second
copy of any skill. The owner's own contracts are never batched: they run as normal calls while a
batch is processing. So do the portfolio questions ("which contracts ..."): each makes a filter
call and, for wording no contract field holds, one table read per contract, which a batch (one
replayed call per unit) cannot carry. The unit runs the reads itself, with the job handler's own
entry point, and asks for the final list the way the page does.

Order of a run (``execute_exam_run``): everything free first -- the exam database is rebuilt, every
document read in, and the requests of phase 1 and the reviews recorded, so the estimate the owner
approves is priced from the real requests (a CUAD contract is not an average) -- and only then the
go-ahead, after which money moves. What a process leaves on disk (``plan.json``, ``state.json``,
``units.jsonl``, ``requests-<phase>.json`` in the run's private folder) is all ``--resume`` needs;
a run whose state says ``submitting`` / ``submitted`` / ``retry_submitted`` / ``submit_failed`` is
WAITING (a batch may exist at the provider), never "crashed" -- ``reconcile_crashed_runs`` and a new
``execute_exam_run`` both leave it alone until it is resumed or abandoned.

Import this module only after ``eval.exam.env.isolate()``: it reaches the app's models, which
import the database engine.
"""

import asyncio
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session, load_only, sessionmaker

from app.ai.citations import align_citation_to_source
from app.ai.clause_segments import clause_skill_input, segments_for_snapshot
from app.ai.context import build_contract_context
from app.ai.controller import ai_controller
from app.ai.models import AISkillRun
from app.ai.registry import skill_registry
from app.ai.schemas import PlaybookReviewOutput
from app.analytics.routes import token_cost
from app.auth.models import User
from app.contract_brain.clause_taxonomy import canonical_clause_type
from app.contract_brain.ingestion import ingest_contract_brain
from app.contract_brain.models import ClauseExtraction
from app.contract_brain.retrieval import sources_to_context
from app.contract_brain.routes import BrainAskRequest, ask_contract_brain, get_brain_query_result
from app.contract_files.models import ContractTextSnapshot, ContractVersion
from app.contracts.models import Contract
from app.core.config import settings
from app.core.database import SessionLocal, new_uuid
from app.core.models import AICallLog, AuditLog
from app.intake.models import IntakeRequest
from app.intake.triage_agent import triage
from app.integrations import claude
from app.jobs import tasks as job_tasks
from app.jobs.models import JobRun
from app.jobs.service import create_job
from app.playbooks.models import Playbook, PlaybookRule
from app.playbooks.service import PlaybooksService, ai_deviations_to_evaluated
from eval.exam import evaldb, report, score
from eval.exam.content import (
    PRIVATE_DIR,
    ExamContent,
    OwnerFactKey,
    SourceCategory,
    Task,
    app_code_sha256,
    content_fingerprint,
    load_content,
)
from eval.exam.env import EVAL_DB_NAME, ExamRefused, LiveDB, Owner, audit, exam_lock

RUNS_DIR = PRIVATE_DIR / "runs"  # <run_id>/state.json, results.json, detail.md

# state.json "step" values under which a batch may be outstanding at the provider. A run in one
# of them is waiting for --resume / --abandon, not crashed: marking it finished would leave a
# paid batch nobody can cancel or replay. ("submitting" and "submit_failed": the create call may
# have succeeded without its reply reaching us.)
_WAITING_STEPS = frozenset({"submitting", "submitted", "retry_submitted", "submit_failed"})

# Anthropic's Message Batches API ids start with this; the stamp reads it to set `batched`.
_BATCH_ID_PREFIX = "msgbatch"

_RUN_OUTCOMES = frozenset({"complete", "incomplete", "stopped"})

# The kinds of paid run (PaidRun.kind). The kind is written into an immutable audit row and
# decides whether the exam database's call records are claimed on crash recovery, so a typo must
# fail before anything is written.
_RUN_KINDS = frozenset({"exam", "ocr", "prefill", "draft_routing"})

# Rows copied to the live ledger per INSERT: ~25 bound parameters each, well under the
# 65,535-parameter limit of one statement.
_MIRROR_CHUNK = 500


class CeilingReached(Exception):
    """A paid call was refused because it could push spend past the ceiling."""


class Deferred(BaseException):
    """Capture mode: the request was recorded for the batch and the app operation is aborted.

    A BaseException ON PURPOSE (plan decision 22): the app's ``except Exception`` fallbacks would
    otherwise write fallback output and a failure row into the exam database for a call that
    was only being recorded. Only the exam's unit wrapper catches it.
    """

    def __init__(self, custom_id: str) -> None:
        super().__init__(custom_id)
        self.custom_id = custom_id


class BatchCallFailed(Exception):
    """Replay: the request has no usable batch answer (errored, expired, canceled, or never
    captured -- which includes "the prompt changed since capture")."""


@dataclass
class Estimate:
    delivery: Literal["batch", "sync"]
    calls: int
    batch_calls: int
    sync_calls: int
    usd: float  # expected, at the price actually paid (batch x0.5)
    list_usd: float  # expected, every call at list price
    worst_case_usd: float  # every answer at max_tokens; the minimum ceiling for a complete run
    by_call_type: dict[str, dict]  # {"<prompt_key>": {"calls": int, "batch": int, "usd": float}}
    ocr_files: int = 0
    ocr_pages: int = 0
    ocr_credits: int = 0
    ocr_usd: float | None = None  # None when REDUCTO_USD_PER_CREDIT is not set


def custom_id(payload: dict) -> str:
    """The Message Batches ``custom_id`` of a request: the sha256 of its exact payload.

    Keying by the request itself means a stored batch answer can only ever go back to the
    identical request -- if the app builds a different prompt at replay, there is no answer for
    it and the call fails instead of being answered with another request's result. 64 hex
    characters is the longest id the API accepts.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def worst_cost(payload: dict, *, batched: bool) -> float:
    """The most this request can cost: input at one token per 3 characters, output at max_tokens.

    Characters of the whole JSON body (system, messages, tools, schema) divided by 3 -- English
    averages about 4 per token, so the extra headroom also covers the 1.25x prompt-cache write
    premium on the cached system prompt. ponytail: a payload that tokenises worse than 3
    characters per token (dense tables, non-Latin script) could cost more than this; the exam's
    contracts are English prose. Swap in the provider's token-count endpoint if that ever fails.
    """
    chars = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    return token_cost(
        math.ceil(chars / 3),
        int(payload.get("max_tokens") or 0),
        payload.get("model"),
        batched=batched,
    )


def _message_cost(payload: dict, message: dict, *, batched: bool) -> float:
    """What a real answer cost, from the usage block the provider returned."""
    usage = message.get("usage") or {}
    return token_cost(
        usage.get("input_tokens") or 0,
        usage.get("output_tokens") or 0,
        message.get("model") or payload.get("model"),
        usage.get("cache_read_input_tokens") or 0,
        usage.get("cache_creation_input_tokens") or 0,
        batched=batched,
    )


class Meter:
    """The exam's one paid-call choke point, installed as ``claude.exam_meter``.

    Hard ceiling (FR-32): a call -- or a batch request -- is admitted only if
    ``spent + reserved + its worst case <= ceiling``, so actual spend can never pass the
    ceiling even though a sent batch cannot be recalled. Three modes, set by the runner:
    ``live`` (a normal request, checked and settled), ``capture`` (record the request for the
    batch and abort the app operation) and ``replay`` (hand back the stored batch answer).
    Calls arrive from several threads (one event loop per unit), so state changes hold a lock.
    """

    def __init__(self, ceiling_usd: float, *, spent_usd: float = 0.0) -> None:
        if not ceiling_usd > 0:
            raise ValueError("the spending ceiling must be greater than zero")
        self.ceiling_usd = ceiling_usd
        self.spent_usd = spent_usd
        self.reserved_usd = 0.0
        self.stopped = False
        self.mode: Literal["live", "capture", "replay"] = "live"
        self.captured: dict[str, dict] = {}  # custom_id -> payload            (capture mode)
        self.results: dict[str, dict] = {}  # custom_id -> batch result dict  (replay mode)
        self.batch_id_of: dict[str, str] = {}  # custom_id -> batch id         (replay mode)
        self._lock = threading.Lock()

    def before(self, payload: dict) -> tuple[dict, str] | None:
        if self.mode == "capture":
            cid = custom_id(payload)
            self.captured[cid] = payload
            raise Deferred(cid)
        if self.mode == "replay":
            return self._replayed(payload)
        cost = worst_cost(payload, batched=False)
        with self._lock:
            # Once the ceiling has stopped the run nothing more is launched, even a call that
            # would still fit: FR-32 says paid calls stop, not "the big ones".
            if self.stopped or self.spent_usd + self.reserved_usd + cost > self.ceiling_usd:
                self.stopped = True
                raise CeilingReached(
                    f"spending ceiling ${self.ceiling_usd:,.2f} reached "
                    f"(spent ${self.spent_usd:,.2f}, reserved ${self.reserved_usd:,.2f}, "
                    f"this call could cost up to ${cost:,.2f})"
                )
            self.reserved_usd += cost
        return None

    def after(self, payload: dict, response: dict | None) -> None:
        if self.mode != "live":
            return
        actual = _message_cost(payload, response, batched=False) if response is not None else 0.0
        with self._lock:
            # Same payload, same worst case: the reservation `before` made is released exactly.
            self.reserved_usd = max(0.0, self.reserved_usd - worst_cost(payload, batched=False))
            self.spent_usd += actual

    def admit(self, payloads: list[dict]) -> list[dict]:
        """Batch admission, in planned order, by worst case (at the batch price).

        Keeps requests while they fit under the ceiling and stops at the first that does not:
        the rest are not attempted. Each admitted request holds its worst case in
        ``reserved_usd`` until ``settle_batch``.
        """
        admitted: list[dict] = []
        with self._lock:
            for payload in payloads:
                cost = worst_cost(payload, batched=True)
                if self.stopped or self.spent_usd + self.reserved_usd + cost > self.ceiling_usd:
                    self.stopped = True
                    break
                self.reserved_usd += cost
                admitted.append(payload)
        return admitted

    def settle_batch(self, admitted: list[dict], results: dict[str, dict]) -> None:
        """Swap each admitted request's worst-case hold for what its answer really cost
        (half price). Requests with no answer -- errored, expired, canceled -- cost nothing."""
        with self._lock:
            for payload in admitted:
                self.reserved_usd = max(0.0, self.reserved_usd - worst_cost(payload, batched=True))
                result = results.get(custom_id(payload)) or {}
                if result.get("type") == "succeeded":
                    self.spent_usd += _message_cost(payload, result["message"], batched=True)

    def _replayed(self, payload: dict) -> tuple[dict, str]:
        cid = custom_id(payload)
        result = self.results.get(cid)
        batch_id = self.batch_id_of.get(cid)
        if result is None or batch_id is None:
            # ponytail: a unit that makes a second AI call while it is replayed lands here too
            # (every unit makes exactly one call today). Add a capture loop if that changes.
            raise BatchCallFailed(
                "no batch answer for this request: it was not captured, so either the unit made "
                "an extra AI call or its prompt changed since capture"
            )
        if result.get("type") != "succeeded":
            detail = ((result.get("error") or {}).get("error") or {}).get("type")
            raise BatchCallFailed(
                f"batch request {result.get('type')}" + (f" ({detail})" if detail else "")
            )
        return result["message"], f"{batch_id}:{cid}"


@dataclass
class PaidRun:
    run_id: str
    kind: Literal["exam", "ocr", "prefill", "draft_routing"]
    owner: Owner
    live: LiveDB
    estimate: Estimate
    ceiling_usd: float | None
    ceiling_credits: int | None  # credits only for OCR without a price
    meter: Meter | None
    # Not in the frozen constructor: defaulted, so callers coded against it are unaffected.
    # FR-39 wants "when it started" in the immutable record of every kind of paid run.
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def describe_estimate(estimate: Estimate, *, kind: str, model: str | None = None) -> str:
    """The estimate as the owner reads it before giving a go-ahead (FR-31, FR-33)."""
    lines = [f"Estimate for this {kind.replace('_', ' ')} run"]
    if estimate.calls:
        lines.append(
            f"  AI calls: {estimate.calls} "
            f"({estimate.batch_calls} batch, {estimate.sync_calls} sync)"
        )
        for key, row in sorted(estimate.by_call_type.items()):
            lines.append(
                f"    {key:<30}{row['calls']:>4} calls ({row.get('batch', 0)} batch)"
                f"  about {_usd(row['usd'])}"
            )
        lines.append(
            f"  Expected cost, at the price you will pay (batch calls at half price): "
            f"{_usd(estimate.usd)}"
        )
        lines.append(f"  Expected cost at list price: {_usd(estimate.list_usd)}")
        lines.append(
            f"  Worst case (every answer as long as it is allowed to be): "
            f"{_usd(estimate.worst_case_usd)}"
        )
    if estimate.ocr_files:
        ocr = f"  OCR: {estimate.ocr_files} files, {estimate.ocr_pages} pages, "
        ocr += f"{estimate.ocr_credits} credits"
        if estimate.ocr_usd is not None:
            ocr += f", about {_usd(estimate.ocr_usd)}"
        else:
            ocr += (
                "\n  OCR price not set - set REDUCTO_USD_PER_CREDIT in eval/private/exam.env "
                "to see dollars; the ceiling is asked in credits"
            )
        lines.append(ocr)
    if model:
        lines.append(f"  Model: {model}")
    lines.append(f"  Delivery: {estimate.delivery}")
    if estimate.delivery == "batch" and estimate.batch_calls:
        lines.append(
            "  Batch results usually arrive within an hour and can take up to 24 hours per "
            "phase (two phases)."
        )
    if estimate.calls:
        lines.append(
            f"  Expected cost is about {_usd(estimate.usd)}. To guarantee the run completes, set "
            f"a ceiling of about {_usd(math.ceil(estimate.worst_case_usd))}: you are charged "
            f"only for what is used."
        )
    return "\n".join(lines)


def _parse_ceiling(raw: str) -> float | None:
    try:
        value = float(raw.strip().lstrip("$").replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


def _person(owner: Owner) -> dict:
    return {"user_id": owner.user_id, "email": owner.email}


def approve_paid_run(
    *,
    kind: str,
    live: LiveDB,
    owner: Owner,
    estimate: Estimate,
    ask: Callable[[str], str],
    extra: dict | None = None,
) -> PaidRun | None:
    """Show the estimate, then take the owner's ceiling, which is the go-ahead (FR-31, FR-32).

    The owner types the most this run may spend; that typed amount is the approval (the owner
    dropped the typed run code on 2026-10-07: the ceiling alone is asked). An empty line or a
    closed stdin is a refusal, and so is a ceiling that is not a positive number. Both outcomes
    are audited BEFORE this returns, so no paid call can precede its approval row.
    ``None`` = refused.

    ``extra`` is recorded verbatim in ``eval.spend_approved`` (it cannot overwrite the fields
    below). The exam run passes ``mode``, ``change``, ``target_area``, ``main_measure`` and
    ``versions_at_start`` (``app_code_sha256``, ``model_under_test``, ``content_fingerprint``);
    ``versions_at_start.model_under_test`` is also shown in the estimate. Installing
    ``claude.exam_meter = run.meter`` is the caller's job; ``finish_paid_run`` removes it.
    """
    if kind not in _RUN_KINDS:
        raise ValueError(f"run kind must be one of {sorted(_RUN_KINDS)}, got '{kind}'")
    extra = extra or {}
    run_id = new_uuid()
    credits = kind == "ocr" and estimate.ocr_usd is None
    model = (extra.get("versions_at_start") or {}).get("model_under_test")
    print(describe_estimate(estimate, kind=kind, model=model))

    def refuse(reason: str) -> None:
        audit(
            live,
            action="eval.spend_refused",
            actor=owner,
            resource_type="eval_run",
            resource_id=run_id,
            after={
                **extra,
                "kind": kind,
                "delivery": estimate.delivery,
                "estimate": asdict(estimate),
                "refused_by": _person(owner),
                "reason": reason,
            },
        )
        print("Not approved: nothing was spent.")

    def ask_line(prompt: str) -> str:
        try:
            return ask(prompt).strip()
        except EOFError:  # no terminal to answer on: the same as pressing Enter
            return ""

    typed = ask_line(
        f"Spending ceiling for run {run_id} in {'credits' if credits else 'USD'} "
        "(press Enter to cancel): "
    )
    if not typed:
        refuse("no go-ahead")
        return None
    ceiling = _parse_ceiling(typed)
    if ceiling is None or (credits and not ceiling.is_integer()):
        refuse("no valid spending ceiling")
        return None

    ceiling_credits = int(ceiling) if credits else None
    ceiling_usd = None if credits else ceiling
    audit(
        live,
        action="eval.spend_approved",
        actor=owner,
        resource_type="eval_run",
        resource_id=run_id,
        after={
            **extra,
            "kind": kind,
            "delivery": estimate.delivery,
            "estimate": asdict(estimate),
            "ceiling_usd": ceiling_usd,
            "ceiling_credits": ceiling_credits,
            "approver": _person(owner),
        },
    )
    return PaidRun(
        run_id=run_id,
        kind=kind,
        owner=owner,
        live=live,
        estimate=estimate,
        ceiling_usd=ceiling_usd,
        ceiling_credits=ceiling_credits,
        # OCR is metered by the credits Reducto reports, not by the Claude hook.
        meter=Meter(ceiling_usd) if ceiling_usd is not None and kind != "ocr" else None,
    )


def finish_paid_run(
    run: PaidRun,
    *,
    outcome: str,
    reason: str | None,
    actual: dict,
    results_sha256: str | None,
    summary: dict | None,
) -> None:
    """Write the run's immutable ``eval.run_finished`` record (FR-39) and release the meter.

    ``audit_log`` rows cannot be edited or deleted, so this row IS the run record: who, when,
    estimate against actual, ceiling, outcome, and the sha256 of the private results file.
    ``summary`` supplies the exam-specific fields (``mode``, ``change``, ``target_area``,
    ``versions``, ``contains_errors``, ``failed_call_rate``, ``batches``, ``headline``); the
    fields the runner cannot choose (outcome, ceiling, who) are not read from it.
    """
    if outcome not in _RUN_OUTCOMES:
        # The record is permanent: a typo here would be stuck in the audit trail for good.
        raise ValueError(f"run outcome must be one of {sorted(_RUN_OUTCOMES)}, got '{outcome}'")
    summary = summary or {}
    audit(
        run.live,
        action="eval.run_finished",
        actor=run.owner,
        resource_type="eval_run",
        resource_id=run.run_id,
        after={
            "kind": run.kind,
            "mode": summary.get("mode"),
            "delivery": run.estimate.delivery,
            "change": summary.get("change"),
            "target_area": summary.get("target_area"),
            "started_by": _person(run.owner),
            "approved_by": _person(run.owner),
            "started_at": run.started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "versions": summary.get("versions"),
            "estimate": asdict(run.estimate),
            "ceiling_usd": run.ceiling_usd,
            "ceiling_credits": run.ceiling_credits,
            "outcome": outcome,
            "incomplete_reason": reason,
            "contains_errors": summary.get("contains_errors"),
            "failed_call_rate": summary.get("failed_call_rate"),
            "actual": actual,
            "batches": summary.get("batches", []),
            "results_sha256": results_sha256,
            "headline": summary.get("headline", {}),
        },
    )
    # A meter must not outlive the run it was approved for.
    if run.meter is not None and claude.exam_meter is run.meter:
        claude.exam_meter = None


# --- the live ledger ---------------------------------------------------------------------

# The only columns of an exam call record that reach the live ledger (FR-34, FR-38): metering
# and identity of the call, nothing that holds contract text or an exam-database id. A
# whitelist on purpose: a column added to AICallLog later is NOT copied until someone adds it
# here, so new text-bearing columns cannot leak by default. Everything else stays SQL NULL --
# input_payload, validated_output, raw_ai_output, validation_error, request_id, job_id,
# skill_run_id, session_id, assistant_run_id, tool_call_id, resource_id, redaction_status.
_MIRRORED_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "provider",
    "model",
    "model_config_hash",
    "prompt_key",
    "prompt_version",
    "prompt_hash",
    "output_schema_name",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "latency_ms",
    "status",
    "validation_status",
    "error_class",
    "stop_reason",
    "provider_request_id",
    "eval_run_id",
    "batched",
    "resource_type",  # a label such as "contract", not an id
)


def mirror_values(row: AICallLog, *, live_org_id: str, owner_id: str) -> dict:
    """The live-ledger copy of one exam call record (pure).

    The live row has the same id, so a repeat copy is a no-op; the live org and the owner replace
    the exam database's org and users. Refuses a row that is not stamped as exam traffic: copied
    unstamped it would be counted as real usage.
    """
    if not row.eval_run_id:
        raise ValueError("an unstamped call record would be copied as real usage; stamp it first")
    values = {name: getattr(row, name) for name in _MIRRORED_COLUMNS}
    values["batched"] = bool(row.batched)
    values.update(org_id=live_org_id, created_by_user_id=owner_id, updated_by_user_id=owner_id)
    return values


def stamp_and_mirror(
    *, run_id: str, exam_sessions: sessionmaker, live: LiveDB, owner: Owner
) -> int:
    """Tag the exam database's new call records with this run, then copy them to the live ledger.

    Stamp: every record still without ``eval_run_id`` is this run's (only one run writes the
    exam database; except a ``CeilingReached`` refusal, which is not a call) and is marked
    ``batched`` when it was answered through a batch -- the replayed
    record's ``provider_request_id`` starts ``msgbatch``. Copy: records the live ledger does not
    have yet, ids and metering only, so the AI Usage page can show exam spend apart from real
    usage. Both steps are safe to repeat, which is what lets crash reconciliation call this
    again. Returns the number of rows copied. Raises ``ExamRefused`` (nothing written) when
    ``exam_sessions`` is not connected to a database named ``legal_clm_eval...``.
    """
    with exam_sessions() as exam:
        # The UPDATE below tags EVERY record without a run id. In the live database those are real
        # usage, so a session bound to anything but the exam database must never reach it.
        database = exam.scalar(select(func.current_database()))
        if not database.startswith(EVAL_DB_NAME):
            raise ExamRefused(
                f"refusing to tag call records in database '{database}': only a database "
                f"named '{EVAL_DB_NAME}...' holds exam traffic"
            )
        exam.execute(
            update(AICallLog)
            # A call the ceiling refused never left the machine; the run forgets its row (it is not
            # a failed call). Left unstamped here, a row that lands between the run's delete and
            # this statement is never copied to the live ledger, whatever the timing.
            .where(
                AICallLog.eval_run_id.is_(None),
                func.coalesce(AICallLog.error_class, "") != CeilingReached.__name__,
            )
            .values(
                eval_run_id=run_id,
                batched=func.coalesce(
                    AICallLog.provider_request_id.startswith(_BATCH_ID_PREFIX), False
                ),
            )
            .execution_options(synchronize_session=False)
        )
        exam.commit()
        exam_ids = set(exam.scalars(select(AICallLog.id).where(AICallLog.eval_run_id == run_id)))
    # One database at a time: the live session is opened only after the exam one is closed.
    with live.sessions() as db:
        have = set(
            db.scalars(
                select(AICallLog.id).where(
                    AICallLog.org_id == live.org_id, AICallLog.eval_run_id == run_id
                )
            )
        )
    missing = sorted(exam_ids - have)
    copied = 0
    for start in range(0, len(missing), _MIRROR_CHUNK):
        with exam_sessions() as exam:
            rows = exam.scalars(
                select(AICallLog)
                # Only the whitelisted columns are loaded: the payload columns hold contract text.
                .options(load_only(*(getattr(AICallLog, name) for name in _MIRRORED_COLUMNS)))
                .where(AICallLog.id.in_(missing[start : start + _MIRROR_CHUNK]))
            ).all()
            values = [
                mirror_values(r, live_org_id=live.org_id, owner_id=owner.user_id) for r in rows
            ]
        with live.sessions() as db:
            # RETURNING, not rowcount: an ORM-enabled multi-row INSERT reports rowcount -1.
            inserted = db.execute(
                pg_insert(AICallLog)
                .values(values)
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(AICallLog.id)
            ).all()
            db.commit()
            copied += len(inserted)
    return copied


def ledger_actual(sessions: sessionmaker, run_id: str, *, org_id: str | None = None) -> dict:
    """What a run's ledger rows say it cost, in the shape of ``eval.run_finished`` ``actual``.

    Priced with the same ``token_cost`` table as the AI Usage page (batch rows at half price).
    ``ocr_usd`` / ``ocr_credits`` are not in the AI ledger; the caller fills them in.
    """
    query = select(
        AICallLog.prompt_key,
        AICallLog.model,
        AICallLog.batched,
        AICallLog.status,
        AICallLog.prompt_tokens,
        AICallLog.completion_tokens,
        AICallLog.cache_read_input_tokens,
        AICallLog.cache_creation_input_tokens,
    ).where(AICallLog.eval_run_id == run_id)
    if org_id is not None:
        query = query.where(AICallLog.org_id == org_id)
    with sessions() as db:
        rows = db.execute(query).all()
    usd = list_usd = 0.0
    failed = 0
    by_call_type: dict[str, dict] = {}
    for r in rows:
        tokens = (
            r.prompt_tokens or 0,
            r.completion_tokens or 0,
            r.model,
            r.cache_read_input_tokens or 0,
            r.cache_creation_input_tokens or 0,
        )
        paid = token_cost(*tokens, batched=bool(r.batched))
        usd += paid
        list_usd += token_cost(*tokens)
        failed += r.status != "succeeded"
        entry = by_call_type.setdefault(
            r.prompt_key or "unlabeled", {"calls": 0, "batch": 0, "usd": 0.0}
        )
        entry["calls"] += 1
        entry["batch"] += bool(r.batched)
        entry["usd"] = round(entry["usd"] + paid, 6)
    return {
        "usd": round(usd, 6),
        "list_usd": round(list_usd, 6),
        "by_call_type": by_call_type,
        "ocr_usd": None,
        "ocr_credits": None,
        "calls": len(rows),
        "failed_calls": failed,
    }


def _waiting_on_batch(run_id: str) -> bool:
    try:
        state = json.loads((RUNS_DIR / run_id / "state.json").read_text())
    except (OSError, ValueError):
        # No record (or an unreadable one) means nothing can resume it; it is not "waiting".
        return False
    return state.get("step") in _WAITING_STEPS


def _unfinished_runs(live: LiveDB) -> list:
    """Approved runs with no ``eval.run_finished``, oldest first (``resource_id``, ``after``,
    ``created_at`` of each ``eval.spend_approved`` row)."""
    with live.sessions() as db:
        finished = set(
            db.scalars(
                select(AuditLog.resource_id).where(
                    AuditLog.org_id == live.org_id, AuditLog.action == "eval.run_finished"
                )
            )
        )
        approved = db.execute(
            select(AuditLog.resource_id, AuditLog.after, AuditLog.created_at)
            .where(AuditLog.org_id == live.org_id, AuditLog.action == "eval.spend_approved")
            .order_by(AuditLog.created_at, AuditLog.id)
        ).all()
    return [row for row in approved if row.resource_id not in finished]


def reconcile_crashed_runs(live: LiveDB, *, exam_sessions: sessionmaker | None) -> list[str]:
    """Close the books on runs that were approved but never finished (FR-40).

    A process that died leaves a ``eval.spend_approved`` row and no ``eval.run_finished``. Each
    such run is finished here as ``incomplete`` / ``crashed`` with the cost its ledger rows show,
    so it can never become part of a baseline. Runs whose ``state.json`` shows a batch that may
    still be outstanding are skipped: they are waiting for ``--resume`` or ``--abandon``.

    Leftover exam-database rows (written after the last per-unit stamp) are stamped and copied to
    the live ledger first. The exam database is rebuilt at the start of every run, so whatever
    is in it belongs to the newest unfinished exam run. ``exam_sessions=None`` means the exam
    database is gone; cost then comes from the rows already copied. Call it under ``exam_lock``:
    with no other exam process alive, an unfinished run is dead, not running.
    """
    open_runs = [row for row in _unfinished_runs(live) if not _waiting_on_batch(row.resource_id)]
    newest_exam = next(
        (r.resource_id for r in reversed(open_runs) if r.after["kind"] == "exam"), None
    )
    reconciled: list[str] = []
    for row in open_runs:
        approved = row.after
        owner = Owner(**approved["approver"])
        if exam_sessions is not None and row.resource_id == newest_exam:
            try:
                stamp_and_mirror(
                    run_id=row.resource_id, exam_sessions=exam_sessions, live=live, owner=owner
                )
            except (OperationalError, ProgrammingError):
                pass  # the exam database is gone or half-built: nothing in it to recover
        actual = ledger_actual(live.sessions, row.resource_id, org_id=live.org_id)
        calls, failed = actual["calls"], actual["failed_calls"]
        run = PaidRun(
            run_id=row.resource_id,
            kind=approved["kind"],
            owner=owner,
            live=live,
            estimate=Estimate(**approved["estimate"]),
            ceiling_usd=approved.get("ceiling_usd"),
            ceiling_credits=approved.get("ceiling_credits"),
            meter=None,
            started_at=row.created_at,
        )
        finish_paid_run(
            run,
            outcome="incomplete",
            reason="crashed",
            actual=actual,
            results_sha256=None,
            summary={
                "mode": approved.get("mode"),
                "change": approved.get("change"),
                "target_area": approved.get("target_area"),
                "versions": approved.get("versions_at_start"),
                "contains_errors": failed > 0,
                "failed_call_rate": failed / calls if calls else 0.0,
            },
        )
        print(f"Run {row.resource_id} never finished; recorded as incomplete (crashed).")
        reconciled.append(row.resource_id)
    return reconciled


# =============================================================================================
# Part 2 -- the exam run
# =============================================================================================

# Routing items are triaged as transient intake requests with this id (never saved). Triage's
# ledger rows carry the request id as their resource id, which is how a call is matched back to its
# item (plan, "Item <-> AI-call linkage").
EXAM_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "ai-exam.aegis.invalid")

POLL_SECONDS = 60  # how often a waiting batch is asked how far it has got
_CANCEL_POLLS = 10  # how many times an abandoned run asks whether its cancelled batch has ended
CONCURRENCY = 4  # units in flight at once

# Batch request errors a second try may cure; the same ones the synchronous path retries. Anything
# else (a rejected request, an expiry) is final.
_TRANSIENT_ERRORS = frozenset({"api_error", "overloaded_error", "rate_limit_error"})

# What a unit's AI call is called in the ledger (the "call type" of every report). A clause unit
# on a document the reader could not segment runs the older ``clause_extraction`` skill instead.
_CALL_TYPE = {
    "clause": "clause_labeling",
    "metadata": "contract_metadata_extraction",
    "review": "playbook_review",
    "qa": "contract_brain_answer",
    "portfolio": "portfolio_filter",  # plus one table read per contract (see build_estimate)
    "routing": "intake_triage",
}

# Expected answer length per call type, for the estimate (the live ledger's averages, plan "Exam
# size, cost and sensitivity"). A previous complete run's real cost replaces them once one exists.
_EXPECTED_OUT_TOKENS = {
    "contract_metadata_extraction": 1_000,
    "clause_labeling": 1_500,
    "clause_extraction": 4_000,
    "playbook_review": 1_500,
    "contract_brain_answer": 1_300,
    "intake_triage": 450,
    "portfolio_filter": 250,  # a typed filter, a few lines of JSON
    "tabular_cell_extraction": 400,  # one yes/no answer with its reason and a quote
}

# A Contract Brain answer's prompt is whatever the app's retrieval returns (a few passages, clauses
# and graph facts), not a function of the contract's length, so its size is a constant: the live
# ledger's average, and an upper bound taken from the retrieval limits (8 passages of 600
# characters per lens plus 40 graph facts, plus the prompt). The clause and graph lenses only exist
# once phase 1 is stored, so these requests cannot be measured before the owner has to decide.
# ponytail: if retrieval's limits grow, this bound goes stale and the worst case understates; the
# ceiling itself is unaffected (it admits each request by its real captured size).
_ASK_EXPECTED_IN_TOKENS = 5_600
_ASK_WORST_IN_CHARS = 36_000

# A portfolio question ("which contracts ...") makes one filter call (the question and the skill's
# prompt, no contract text), then, for wording no contract field answers, one table read per
# contract (the whole text plus a short prompt). Sizes are constants for the filter and the
# read's prompt, measured on the requests the app builds (the filter's is about 6,800 characters, a
# read's prompt about 3,900 before the contract); a read's text is the real length of the contract.
# The filter's worst case adds the longest question the route accepts (8,000 characters).
_FILTER_EXPECTED_IN_TOKENS = 1_700
_FILTER_WORST_IN_CHARS = 15_000
_READ_PROMPT_TOKENS = 1_000
_READ_PROMPT_CHARS = 5_000
# The facts a contract record has its own field for (type, other party, governing law, end date,
# value): the filter decides those from the record, so the question reads no contract. Any other
# fact ("renews automatically", "needs 90 days' notice") is wording, and every contract is read.
# ponytail: mirrors what the filter has fields for, and only prices the run; the ceiling does not
# depend on it. A fact the app learns a field for is one word more here.
_FIELD_FACTS = frozenset(
    {"agreement_type", "counterparty", "governing_law", "expiry_year", "value"}
)


class RunPaused(ExamRefused):
    """The run is waiting on a person or a provider and has NOT finished: nothing is lost, and the
    message says whether to ``--resume`` or ``--abandon`` it. (Exit code 2, like a refusal.)"""


# --- what a run asks ------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    """One piece of work that makes (at most) one AI call, in a fixed place in the run."""

    unit_id: str  # cl-<doc> | md-<doc> | rt-NN | rv-<doc> | qa-NNN | pq-NN
    kind: Literal["clause", "metadata", "routing", "review", "qa", "portfolio"]
    phase: int  # 1 = labelling, metadata, routing; 2 = review, answers (they read phase 1's output)
    doc_id: str | None
    delivery: Literal["batch", "sync"]
    ref: str | None = None  # a review unit's playbook


@dataclass(frozen=True)
class Item:
    """One scored thing. Its answer comes from ``own`` units; ``docs`` are the documents whose
    phase-1 calls it reads (an extraction item has no unit of its own: it reads what they
    stored)."""

    item_id: str
    task: str  # a Task value
    doc_id: str | None
    own: tuple[str, ...]
    docs: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    mode: str
    delivery: str
    doc_ids: tuple[str, ...]  # documents to read in, in content order
    portfolio_doc_ids: tuple[str, ...]  # what the portfolio user will see
    units: tuple[Unit, ...]  # in planned order: phase 1 then phase 2; admission follows it
    items: tuple[Item, ...]
    excluded: dict


def _review_key(content: ExamContent, item_id: str):
    """The review answer key of ``rv-<doc>``; a scanned copy shares its original's record."""
    by_id = {r.item_id: r for r in content.review}
    if item_id in by_id:
        return by_id[item_id]
    doc = next((d for d in content.documents if d.doc_id == item_id.removeprefix("rv-")), None)
    return next((r for r in content.review if doc and r.doc_id == doc.twin_of), None)


def plan_run(
    content: ExamContent,
    mode: Literal["sample", "full"],
    delivery: Literal["batch", "sync"],
    *,
    stale_owner_keys: Sequence[OwnerFactKey] = (),
    routing_current: bool = True,
) -> Plan:
    """What a run asks, in an order fixed by the content alone (FR-1, FR-30, FR-41).

    Full = every item; sample = the fixed subset in ``sample.json``, plus -- from the owner's
    private manifest -- one ordinary contract and one stamp-only scan with its "as read today" twin.
    Left out, and counted: owner keys that no longer match their file, rows the owner left
    unmarked, owner contracts left with no usable key, requests the owner did not pick a workflow
    for, and routing altogether when the workflow list has changed since the owner picked.
    Owner contracts and portfolio questions are always ``sync`` (FR-41; a portfolio question
    makes several calls); everything else is ``batch`` under ``delivery="batch"``.
    """
    full = mode == "full"
    docs = {d.doc_id: d for d in content.documents}
    outside = "sync" if delivery == "sync" else "batch"

    def pick(task: Task, rows):
        wanted = set(content.sample.get(task.value, []))
        return list(rows) if full else [r for r in rows if r.item_id in wanted]

    items: list[Item] = []
    needed: set[str] = set()
    review_units: list[Unit] = []
    qa_units: list[Unit] = []
    portfolio_units: list[Unit] = []
    routing_units: list[Unit] = []

    # Review, including the scanned copies: they share their original's answer record.
    originals = {r.doc_id for r in content.review}
    sample_review = set(content.sample.get(Task.REVIEW.value, []))
    review: list[tuple[str, str]] = [
        (r.item_id, r.doc_id) for r in pick(Task.REVIEW, content.review)
    ]
    twins = [
        d
        for d in content.documents
        if d.twin_of in originals and (full or f"rv-{d.doc_id}" in sample_review)
    ]
    review += [(f"rv-{d.doc_id}", d.doc_id) for d in twins]
    for item_id, doc_id in review:
        key = _review_key(content, item_id)
        items.append(Item(item_id, Task.REVIEW.value, doc_id, (item_id,), (doc_id,)))
        review_units.append(Unit(item_id, "review", 2, doc_id, outside, key.playbook))
        needed.add(doc_id)

    # Extraction: public and constructed keys, the scanned copies, then the owner's contracts.
    extraction = pick(Task.EXTRACTION, content.extraction)
    planned = {e.item_id for e in extraction}
    for e in extraction:
        items.append(Item(e.item_id, Task.EXTRACTION.value, e.doc_id, (), (e.doc_id,)))
        needed.add(e.doc_id)
    for d in twins:
        if f"ex-{d.twin_of}" in planned:
            items.append(Item(f"ex-{d.doc_id}", Task.EXTRACTION.value, d.doc_id, (), (d.doc_id,)))

    stale = {(k.doc_id, k.field) for k in stale_owner_keys}
    usable = Counter(
        k.doc_id
        for k in content.owner_keys
        if k.status != "unmarked" and (k.doc_id, k.field) not in stale
    )
    mains = [
        d
        for d in content.documents
        if d.category is SourceCategory.OWNER and not d.as_read_today_of
    ]
    keyed = [d for d in mains if usable[d.doc_id]]
    if not full:
        kind = {e.doc_id: e.kind for e in content.owner_manifest}
        ordinary = next((d for d in keyed if kind.get(d.doc_id) != "stamp_only_scan"), None)
        stamp_only = next((d for d in keyed if kind.get(d.doc_id) == "stamp_only_scan"), None)
        keyed = [d for d in (ordinary, stamp_only) if d is not None]
    owner_ids = {d.doc_id for d in keyed}
    for d in content.documents:
        if d.category is SourceCategory.OWNER and (d.as_read_today_of or d.doc_id) in owner_ids:
            items.append(Item(f"ex-{d.doc_id}", Task.EXTRACTION.value, d.doc_id, (), (d.doc_id,)))
            needed.add(d.doc_id)

    for q in pick(Task.QA, content.qa):
        items.append(Item(q.item_id, Task.QA.value, q.doc_id, (q.item_id,), (q.doc_id,)))
        qa_units.append(Unit(q.item_id, "qa", 2, q.doc_id, outside))
        needed.add(q.doc_id)

    questions = pick(Task.PORTFOLIO, content.portfolio)
    portfolio_ids: tuple[str, ...] = ()
    if questions:
        portfolio_ids = tuple(
            i
            for i in (
                [d.doc_id for d in content.documents if d.uploader == "portfolio"]
                if full
                else content.sample.get("portfolio_docs", [])
            )
            if i in docs
        )
        needed.update(portfolio_ids)
    for q in questions:
        items.append(Item(q.item_id, Task.PORTFOLIO.value, None, (q.item_id,), portfolio_ids))
        # Always a direct unit, never part of a batch: a "which contracts" question makes a filter
        # call and then one read per contract, and a batch replays exactly one call per unit.
        portfolio_units.append(Unit(q.item_id, "portfolio", 2, None, "sync"))

    picked = {k.item_id for k in content.routing_keys if k.status != "unmarked"}
    for r in pick(Task.ROUTING, content.routing) if routing_current else []:
        if r.item_id in picked:
            items.append(Item(r.item_id, Task.ROUTING.value, None, (r.item_id,), ()))
            routing_units.append(Unit(r.item_id, "routing", 1, None, outside))

    ordered = [d for d in content.documents if d.doc_id in needed]
    doc_units: list[Unit] = []
    for d in ordered:
        how = "sync" if d.category is SourceCategory.OWNER or delivery == "sync" else "batch"
        # Clause labelling before metadata: labelling changes none of the metadata prompt's inputs,
        # but metadata fills the contract fields that the labelling prompt carries -- so this is the
        # order that makes the captured requests the ones the replay will build.
        for step, kind, prefix in (("clauses", "clause", "cl"), ("metadata", "metadata", "md")):
            if step in d.ai_steps:
                doc_units.append(Unit(f"{prefix}-{d.doc_id}", kind, 1, d.doc_id, how))

    return Plan(
        mode=mode,
        delivery=delivery,
        doc_ids=tuple(d.doc_id for d in ordered),
        portfolio_doc_ids=portfolio_ids,
        units=(*doc_units, *routing_units, *review_units, *qa_units, *portfolio_units),
        items=tuple(items),
        excluded={
            "owner_keys_out_of_date": sorted(f"{k.doc_id}:{k.field}" for k in stale_owner_keys),
            "owner_rows_unmarked": sum(k.status == "unmarked" for k in content.owner_keys),
            "owner_docs_without_keys": [d.doc_id for d in mains if not usable[d.doc_id]],
            "routing_unpicked": len(content.routing)
            - len(picked & {r.item_id for r in content.routing}),
            "routing_out_of_date": bool(content.routing_keys) and not routing_current,
        },
    )


def _plan_to_json(plan: Plan) -> dict:
    return asdict(plan)


def _plan_from_json(data: dict) -> Plan:
    return Plan(
        mode=data["mode"],
        delivery=data["delivery"],
        doc_ids=tuple(data["doc_ids"]),
        portfolio_doc_ids=tuple(data["portfolio_doc_ids"]),
        units=tuple(Unit(**u) for u in data["units"]),
        items=tuple(
            Item(i["item_id"], i["task"], i["doc_id"], tuple(i["own"]), tuple(i["docs"]))
            for i in data["items"]
        ),
        excluded=data["excluded"],
    )


# --- the estimate: real requests, not averages ------------------------------------------------


def _json_chars(payload: dict) -> int:
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def _stand_in(model: str, skill: str, chars: int) -> dict:
    """A request as large as ``skill``'s largest can be, for the worst case of a call that cannot be
    measured before the go-ahead."""
    max_tokens = skill_registry.get(skill).max_tokens
    ceiling = settings.claude_max_tokens_ceiling
    if ceiling and ceiling > 0:
        max_tokens = min(max_tokens, ceiling)
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": "x" * chars}],
    }


def _ask_stand_in(model: str) -> dict:
    """A request as large as the largest Contract Brain answer can be, for its worst case."""
    return _stand_in(model, "contract_brain_answer", _ASK_WORST_IN_CHARS)


def build_estimate(
    plan: Plan,
    payloads: dict[str, dict],
    *,
    model: str,
    call_types: dict[str, str] | None = None,
    per_call: dict[str, float] | None = None,
    reads: dict[str, Sequence[int]] | None = None,
) -> Estimate:
    """What the run will cost, from the requests it will really send (FR-31, FR-33).

    ``payloads`` are the requests the app built for each unit in a free capture, measured as sent:
    a CUAD contract's real clause list and 25,000-character context, not an average. Input tokens
    are one per 4 characters (expected) and one per 3 (worst case, ``worst_cost``); output is the
    call type's expected length (worst case: ``max_tokens``). Contract Brain answers carry the
    constants above. ``per_call`` -- a previous complete run's mean paid cost per call, by call
    type -- replaces the expected figure of a type it knows. Batch units are priced at half.

    A portfolio unit is one filter call (a constant) plus one table read per entry of ``reads[unit]``
    -- the text length of each contract it is expected to read -- all at full price (a portfolio
    unit is never batched). Its worst case also holds the search answer it falls back to when the
    filter says the question is not a list question.
    """
    call_types = call_types or {}
    reads = reads or {}
    per_type: dict[str, dict] = {}
    totals = {"calls": 0, "batch": 0, "usd": 0.0, "list": 0.0, "worst": 0.0}

    def add(call_type: str, tokens_in: int, unit_model: str, worst: float, batched: bool) -> None:
        out = _EXPECTED_OUT_TOKENS.get(call_type, 1_000)
        paid = token_cost(tokens_in, out, unit_model, batched=batched)
        listed = token_cost(tokens_in, out, unit_model)
        if per_call and per_call.get(call_type) and paid:
            listed, paid = listed * per_call[call_type] / paid, per_call[call_type]
        row = per_type.setdefault(call_type, {"calls": 0, "batch": 0, "usd": 0.0})
        row["calls"] += 1
        row["batch"] += batched
        row["usd"] += paid
        totals["calls"] += 1
        totals["batch"] += batched
        totals["usd"] += paid
        totals["list"] += listed
        totals["worst"] += worst

    for unit in plan.units:
        payload = payloads.get(unit.unit_id)
        call_type = call_types.get(unit.unit_id) or _CALL_TYPE[unit.kind]
        batched = unit.delivery == "batch"
        if unit.kind == "portfolio":
            add(
                call_type,
                _FILTER_EXPECTED_IN_TOKENS,
                model,
                worst_cost(_stand_in(model, "portfolio_filter", _FILTER_WORST_IN_CHARS), batched=False),
                False,
            )
            for chars in reads.get(unit.unit_id, ()):
                add(
                    "tabular_cell_extraction",
                    chars // 4 + _READ_PROMPT_TOKENS,
                    model,
                    worst_cost(
                        _stand_in(model, "tabular_cell_extraction", chars + _READ_PROMPT_CHARS),
                        batched=False,
                    ),
                    False,
                )
            totals["worst"] += worst_cost(_ask_stand_in(model), batched=False)
            continue
        if payload is not None:
            tokens_in = math.ceil(_json_chars(payload) / 4)
            unit_model = payload.get("model") or model
            worst = worst_cost(payload, batched=batched)
        elif unit.kind == "qa":
            tokens_in, unit_model = _ASK_EXPECTED_IN_TOKENS, model
            worst = worst_cost(_ask_stand_in(model), batched=batched)
        else:
            continue  # a unit that makes no AI call
        add(call_type, tokens_in, unit_model, worst, batched)
    return Estimate(
        delivery=plan.delivery,
        calls=totals["calls"],
        batch_calls=totals["batch"],
        sync_calls=totals["calls"] - totals["batch"],
        usd=round(totals["usd"], 4),
        list_usd=round(totals["list"], 4),
        worst_case_usd=round(totals["worst"], 4),
        by_call_type={k: {**v, "usd": round(v["usd"], 4)} for k, v in sorted(per_type.items())},
    )


def _calibration(live: LiveDB, mode: str, delivery: str) -> tuple[str | None, dict[str, float]]:
    """The newest complete run of this mode and delivery, and what each call type cost per call."""
    with live.sessions() as db:
        rows = db.execute(
            select(AuditLog.resource_id, AuditLog.after)
            .where(AuditLog.org_id == live.org_id, AuditLog.action == "eval.run_finished")
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        ).all()
    for run_id, after in rows:
        done = (
            after.get("kind") == "exam"
            and after.get("mode") == mode
            and after.get("delivery") == delivery
            and after.get("outcome") == "complete"
        )
        if done:
            by_type = (after.get("actual") or {}).get("by_call_type") or {}
            return run_id, {k: v["usd"] / v["calls"] for k, v in by_type.items() if v.get("calls")}
    return None, {}


# --- the run itself ---------------------------------------------------------------------------

# Contract fields and stored clauses an extraction item is read from, after phase 1 (plan,
# "Component design" -> runner, step 8). Clause fields name the clause types a person would look
# in for that fact.
_CLAUSE_FIELDS = {
    "renewal_term": ("renewal", "term_and_termination"),
    "notice_period": ("renewal", "term_and_termination", "notices"),
    "liability_cap": ("limitation_of_liability",),
    "termination_for_convenience": ("term_and_termination",),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_atomic(path: Path, text: str) -> None:
    """Write-then-rename, so a crash never leaves half a state file for ``--resume`` to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def _transient(result: dict | None) -> bool:
    error = ((result or {}).get("error") or {}).get("error") or {}
    return (result or {}).get("type") == "errored" and error.get("type") in _TRANSIENT_ERRORS


def _exam_sessions() -> sessionmaker:
    """Sessions on the exam database: this process's default engine, which ``isolate()`` moved."""
    return SessionLocal


def _rebuild_exam_db(live: LiveDB, eval_db_name: str) -> None:
    evaldb.reset(live, eval_db_name)
    evaldb.migrate()


def _attribute(found, rules: list, planted: list[tuple[str, str]]) -> tuple[str | None, str]:
    """Which playbook rule a deviation is about: the rule the AI numbered; else the rule whose
    clause type is the same after canonicalising; else the rule whose planted text the quote
    matches (the answer key is only read to LABEL a flag, never to decide whether one is right);
    else none."""
    if found.rule is not None:
        return found.rule.clause_type, "rule_index"
    wanted = canonical_clause_type(found.clause_type)
    for rule in rules:
        if canonical_clause_type(rule.clause_type) == wanted:
            return rule.clause_type, "clause_type"
    quote = ((found.citation or {}).get("quote") or found.original_text or "").strip()
    if quote:
        for name, text in planted:
            if align_citation_to_source(quote, text)[1] >= 90:
                return name, "text"
    return None, "none"


class _Run:
    """One exam run in progress.

    Everything that must outlive the process is on disk in the run's private folder:
    ``plan.json`` (what was asked, once), ``state.json`` (where the run is: phase, step, each
    unit's status, each batch), ``units.jsonl`` (each finished unit's answer) and, per phase, the
    requests that went into the batch (``requests-<n>.json``). ``--resume`` rebuilds a ``_Run``
    from those and the exam database, which is why nothing here may live only in memory.
    """

    def __init__(
        self,
        *,
        live: LiveDB,
        owner: Owner,
        content: ExamContent,
        plan: Plan,
        sessions: sessionmaker,
        ask: Callable[[str], str],
        change: str | None,
        target: str | None,
    ) -> None:
        self.live, self.owner, self.content, self.plan = live, owner, content, plan
        self.sessions, self.ask, self.change, self.target = sessions, ask, change, target
        self.docs_by_id = {d.doc_id: d for d in content.documents}
        self.qa_by_id = {q.item_id: q for q in content.qa}
        self.portfolio_by_id = {q.item_id: q for q in content.portfolio}
        self.request_by_id = {r.item_id: r for r in content.routing}
        self.org_id = self.runner_id = self.portfolio_id = ""
        self.playbooks: dict[str, str] = {}
        self.ingested: dict[str, evaldb.IngestedDoc] = {}
        self.doc_failure: dict[str, str] = {}
        self.payloads: dict[str, dict] = {}  # unit -> the request its capture recorded
        self.cid_of: dict[str, str] = {}  # unit -> custom_id of that request
        self.by_cid: dict[str, dict] = {}  # custom_id -> request (what the batch carries)
        self.call_type: dict[str, str] = {}  # unit -> call type, when it is not the default
        self.predictions: dict[str, dict] = {}
        self.unit_failure: dict[str, str] = {}
        self.refused: set[str] = set()  # units whose call the ceiling refused
        self.meter = Meter(1.0)
        self.paid: PaidRun | None = None
        self.state: dict = {}
        self.dir: Path | None = None
        self._lock = asyncio.Lock()
        self._texts: dict[str, str] = {}
        self.ingest_seconds = 0.0
        self._rebind()

    def _rebind(self) -> None:
        """Lookups that follow the plan (it is trimmed once the documents are read)."""
        self.units_by_id = {u.unit_id: u for u in self.plan.units}
        self.route_of = {
            str(uuid.uuid5(EXAM_NS, u.unit_id)): u.unit_id
            for u in self.plan.units
            if u.kind == "routing"
        }
        self.doc_units: dict[str, list[str]] = defaultdict(list)
        for u in self.plan.units:
            if u.phase == 1 and u.doc_id:
                self.doc_units[u.doc_id].append(u.unit_id)
        self.item_docs = {i.item_id: i.docs for i in self.plan.items}

    @property
    def run_id(self) -> str:
        return self.paid.run_id  # type: ignore[union-attr]

    # --- before the go-ahead: free steps ----------------------------------------------------

    @contextmanager
    def _capture_only(self):
        """Run under a capture-mode meter: any AI request raises ``Deferred`` instead of leaving.

        Everything before the go-ahead is free (FR-31) -- reading a file makes no AI call -- but
        that is a property of today's ingestion code, not something the run enforces. With this
        installed, a later change that makes reading a paid call stops the run before anything
        is sent, unmetered and unapproved, instead of spending quietly.
        """
        claude.exam_meter = self.meter = Meter(1.0)
        self.meter.mode = "capture"
        try:
            yield
        except Deferred:
            raise ExamRefused(
                "reading the exam's documents tried to make a paid AI call, which cannot happen "
                "before the go-ahead. Nothing was sent or spent."
            ) from None
        finally:
            claude.exam_meter = None

    async def prepare(self) -> None:
        """Build the exam database's contents and read every document in. No paid call can happen
        here (FR-31): ingestion only reads files, and a capture-mode meter stops it if that changes."""
        from eval.exam.owner import FrozenOCR  # owner imports this module

        began = time.monotonic()
        with self._capture_only():
            with self.sessions() as db:
                actors = evaldb.bootstrap(db)
            self.org_id, self.runner_id = actors.org_id, actors.runner.id
            self.portfolio_id = actors.portfolio.id
            with self.sessions() as db:
                self.playbooks = evaldb.load_playbooks(db, self.content, actors)
            if any(u.kind == "routing" for u in self.plan.units):
                with self.sessions() as db:
                    evaldb.load_workflows(db, self.content.workflow_snapshot, actors)
            ocr = FrozenOCR()  # no provider: reading a file can never pay for OCR
            for number, doc_id in enumerate(self.plan.doc_ids, start=1):
                if number % 10 == 0:
                    print(f"  read {number} of {len(self.plan.doc_ids)} documents")
                try:
                    with self.sessions() as db:
                        self.ingested[doc_id] = await evaldb.ingest_document(
                            db, self.docs_by_id[doc_id], actors=actors, ocr=ocr
                        )
                except evaldb.IngestFailed as exc:
                    self.doc_failure[doc_id] = exc.reason
        self._settle_plan()
        self.ingest_seconds = time.monotonic() - began

    def _settle_plan(self) -> None:
        """Drop what cannot run now that the documents have been read.

        An owner contract whose text no longer equals the frozen text its answers were confirmed
        against has stale keys (FR-20): it and its "as read today" twin leave the run, and the keys
        are listed as out of date. A document that could not be read keeps its items (they are
        reported failed, FR-40) but sends nothing.
        """
        stale = {d for d, ing in self.ingested.items() if ing.frozen_text_matches is False}
        gone = stale | {d.doc_id for d in self.content.documents if d.as_read_today_of in stale}
        broken = set(self.doc_failure)
        item_docs = {i.item_id: set(i.docs) for i in self.plan.items}

        def dead(unit: Unit) -> bool:
            return bool(
                {unit.doc_id} & (gone | broken)
                or item_docs.get(unit.unit_id, set()) & (gone | broken)
            )

        excluded = dict(self.plan.excluded)
        excluded["owner_keys_out_of_date"] = sorted(
            {*excluded["owner_keys_out_of_date"]}
            | {f"{k.doc_id}:{k.field}" for k in self.content.owner_keys if k.doc_id in stale}
        )
        self.plan = replace(
            self.plan,
            units=tuple(u for u in self.plan.units if not dead(u)),
            items=tuple(i for i in self.plan.items if i.doc_id not in gone),
            excluded=excluded,
        )
        self._rebind()

    async def preview(self) -> None:
        """Record, free, the requests phase 1 and the reviews will send, so the estimate is priced
        from them. A capture meter raises before any request leaves, so this cannot spend."""
        units = [u for u in self.plan.units if u.phase == 1 or u.kind == "review"]
        with self._capture_only():
            status = await self._run_chains(units, "capture", record=False)
        # A unit that cannot even build its request is missing from the estimate (it will fail in
        # the run too, and be reported); say so rather than let the figure look complete.
        broken = [unit_id for unit_id, outcome in status.items() if outcome == "failed"]
        for unit_id in broken[:5]:
            print(f"  WARNING: {unit_id} could not be prepared ({self.unit_failure.get(unit_id)})")
        if broken:
            print(f"  {len(broken)} unit(s) could not be prepared and are not in the estimate.")

    def expected_reads(self) -> dict[str, list[int]]:
        """For each portfolio question that asks about wording no contract field answers, the text
        length of every portfolio contract: the AI is expected to read them all. A question about
        a fact the record has a field for reads none. Only prices the run (``build_estimate``)."""
        every = [self.ingested[d].text_len for d in self.plan.portfolio_doc_ids if d in self.ingested]
        return {
            u.unit_id: every
            for u in self.plan.units
            if u.kind == "portfolio"
            and self.portfolio_by_id[u.unit_id].fact not in _FIELD_FACTS
        }

    def start(self, paid: PaidRun) -> None:
        """The owner said go: install the real meter and write down what was asked."""
        self.paid, self.meter = paid, paid.meter  # type: ignore[assignment]
        claude.exam_meter = self.meter
        self.dir = RUNS_DIR / paid.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        documents = {d: {"status": "ok", **asdict(ing)} for d, ing in self.ingested.items()} | {
            d: {"status": "failed", "failure": why} for d, why in self.doc_failure.items()
        }
        _write_atomic(
            self.dir / "plan.json",
            json.dumps(
                {
                    **_plan_to_json(self.plan),
                    "documents": documents,
                    "playbooks": self.playbooks,
                    "ids": [self.org_id, self.runner_id, self.portfolio_id],
                    "change": self.change,
                    "target": self.target,
                }
            ),
        )
        self.state = {
            "run_id": paid.run_id,
            "mode": self.plan.mode,
            "delivery": self.plan.delivery,
            "ceiling_usd": paid.ceiling_usd,
            "phase": 1,
            "step": "ingested",
            "units": {u.unit_id: "pending" for u in self.plan.units},
            "batches": [],
            "started_at": paid.started_at.isoformat(),
            "phase_wall_time_s": {"ingest": round(self.ingest_seconds, 3)},
            "brain_failures": {},
        }
        self._save()

    @classmethod
    def load(
        cls,
        *,
        live: LiveDB,
        owner: Owner,
        content: ExamContent,
        paid: PaidRun,
        sessions: sessionmaker,
        ask: Callable[[str], str],
    ) -> "_Run":
        """Rebuild a run from its folder (``--resume``)."""
        folder = RUNS_DIR / paid.run_id
        data = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
        run = cls(
            live=live,
            owner=owner,
            content=content,
            plan=_plan_from_json(data),
            sessions=sessions,
            ask=ask,
            change=data.get("change"),
            target=data.get("target"),
        )
        run.paid, run.meter, run.dir = paid, paid.meter, folder  # type: ignore[assignment]
        run.org_id, run.runner_id, run.portfolio_id = data["ids"]
        run.playbooks = data["playbooks"]
        for doc_id, record in data["documents"].items():
            if record["status"] == "ok":
                run.ingested[doc_id] = evaldb.IngestedDoc(
                    **{k: v for k, v in record.items() if k != "status"}
                )
            else:
                run.doc_failure[doc_id] = record["failure"]
        run.state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        log = folder / "units.jsonl"
        for line in log.read_text(encoding="utf-8").splitlines() if log.is_file() else []:
            row = json.loads(line)
            if row["prediction"] is not None:
                run.predictions[row["unit_id"]] = row["prediction"]
            if row["reason"]:
                run.unit_failure[row["unit_id"]] = row["reason"]
        for index in {b["phase"] for b in run.state["batches"]}:
            run._read_requests(index)
        # The ceiling is a hard limit (FR-32) only if a resumed run admits what the run that died
        # would have: a batch still in flight holds its worst case until its answers are settled,
        # and the owner's direct calls that were still pending are admitted next to that hold. The
        # ledger's spend does not know about requests that have not been replayed yet.
        run.meter.reserved_usd += sum(
            v["worst_usd"]
            for b in run.state["batches"]
            if b["batch_id"] and not b["collected"]
            for v in b["requests"].values()
            if run.state["units"][v["unit_id"]] == "captured"
        )
        return run

    # --- state on disk ---------------------------------------------------------------------

    def _save(self) -> None:
        _write_atomic(self.dir / "state.json", json.dumps(self.state))  # type: ignore[operator]

    def _set(self, **changes) -> None:
        self.state.update(changes)
        self._save()

    def _record(self, unit: Unit, status: str) -> None:
        """A unit's outcome, on disk at once: a process that dies re-runs only what is not here."""
        self.state["units"][unit.unit_id] = status
        if status in ("done", "failed", "not_attempted"):
            line = {
                "unit_id": unit.unit_id,
                "status": status,
                "prediction": self.predictions.get(unit.unit_id),
                "reason": self.unit_failure.get(unit.unit_id),
            }
            log = self.dir / "units.jsonl"  # type: ignore[operator]
            with open(log, "a", encoding="utf-8") as out:
                out.write(json.dumps(line) + "\n")
        self._save()

    def _requests_path(self, phase: int) -> Path:
        return self.dir / f"requests-{phase}.json"  # type: ignore[operator]

    def _write_requests(self, phase: int, wanted: dict[str, str]) -> None:
        path = self._requests_path(phase)
        have = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        have.update({cid: self.by_cid[cid] for cid in wanted})
        _write_atomic(path, json.dumps(have))

    def _read_requests(self, phase: int) -> None:
        path = self._requests_path(phase)
        if path.is_file():
            self.by_cid.update(json.loads(path.read_text(encoding="utf-8")))

    def _add_wall_time(self, key: str, since: float) -> None:
        spent = self.state["phase_wall_time_s"]
        spent[key] = round(spent.get(key, 0.0) + time.monotonic() - since, 3)
        self._save()

    # --- one unit: the app's own code, one AI call at most ---------------------------------

    async def _run_chains(self, units: list[Unit], mode: str, *, record: bool) -> dict[str, str]:
        """Run units, a few at a time. A document's labelling and metadata run one after the other
        (labelling first); every other unit is its own chain. Returns each unit's status."""
        out: dict[str, str] = {}
        chains: dict[str, list[Unit]] = {}
        for unit in units:
            chains.setdefault(
                unit.doc_id if unit.phase == 1 and unit.doc_id else unit.unit_id, []
            ).append(unit)
        gate = asyncio.Semaphore(CONCURRENCY)

        async def chain(members: list[Unit]) -> None:
            async with gate:
                for unit in members:
                    out[unit.unit_id] = await self._one(unit, mode, record)

        outer = self.meter.mode
        self.meter.mode = mode  # type: ignore[assignment]
        try:
            await asyncio.gather(*(chain(m) for m in chains.values()))
        finally:
            self.meter.mode = outer  # "capture" stays capture inside the free stage
        return out

    async def _one(self, unit: Unit, mode: str, record: bool) -> str:
        if mode == "live" and self.meter.stopped:
            status = "not_attempted"  # the ceiling already stopped the run: nothing more is sent
        else:
            status = await self._run_unit(unit)
            if mode != "capture":
                await self._sync_ledger()
                if unit.unit_id in self.refused:
                    status = "not_attempted"
        if record:
            self._record(unit, status)
        return status

    async def _run_unit(self, unit: Unit) -> str:
        """``captured`` (its request was recorded, nothing sent), ``done``, ``failed`` or
        ``not_attempted``. Whether its CALL succeeded is read from the ledger afterwards: the app
        turns many provider failures into fallbacks that return normally."""
        try:
            with self.sessions() as db:
                prediction = await self._execute(db, unit)
        except Deferred as recorded:
            self.cid_of[unit.unit_id] = recorded.custom_id
            self.payloads[unit.unit_id] = self.by_cid[recorded.custom_id] = self.meter.captured[
                recorded.custom_id
            ]
            return "captured"
        except CeilingReached:
            return "not_attempted"
        except Exception as exc:
            self.unit_failure[unit.unit_id] = f"unit failed: {type(exc).__name__}: {str(exc)[:300]}"
            return "failed"
        self.unit_failure.pop(unit.unit_id, None)  # an earlier try (the preview) may have failed
        if prediction is not None:
            self.predictions[unit.unit_id] = prediction
        return "done"

    async def _execute(self, db: Session, unit: Unit) -> dict | None:
        if unit.kind in ("clause", "metadata"):
            return await self._document_skill(db, unit)
        if unit.kind == "review":
            return await self._review(db, unit)
        if unit.kind in ("qa", "portfolio"):
            return await self._ask(db, unit)
        return await self._route(db, unit)

    async def _document_skill(self, db: Session, unit: Unit) -> None:
        """The job handlers' own call (``tasks._run_ai_job``): a job row, then ``run_job_skill``.
        The job is never dispatched; none of the handler's follow-up jobs are queued."""
        doc, ing = self.docs_by_id[unit.doc_id], self.ingested[unit.doc_id]  # type: ignore[index]
        job = create_job(
            db,
            org_id=self.org_id,
            job_type="clause_extraction" if unit.kind == "clause" else "metadata_extraction",
            resource_type="contract",
            resource_id=ing.contract_id,
            created_by_user_id=self.portfolio_id if doc.uploader == "portfolio" else self.runner_id,
            idempotency_key=f"exam:{unit.unit_id}",  # the exam database is new every run
            metadata={"contract_version_id": ing.version_id, "text_snapshot_id": ing.snapshot_id},
        )
        db.flush()
        if unit.kind == "clause":
            skill, payload = clause_skill_input(
                db,
                contract_id=ing.contract_id,
                contract_version_id=ing.version_id,
                text_snapshot_id=ing.snapshot_id,
            )
            self.call_type[unit.unit_id] = skill
        else:
            skill = "contract_metadata_extraction"
            payload = {
                "contract_id": ing.contract_id,
                "contract_version_id": ing.version_id,
                "text_snapshot_id": ing.snapshot_id,
            }
        await ai_controller.run_job_skill(db, job=job, skill_name=skill, input_payload=payload)

    async def _review(self, db: Session, unit: Unit) -> dict:
        """The playbook review the app runs after clause extraction (``auto_review_contract``),
        and its deviations read the way the app reads them."""
        ing = self.ingested[unit.doc_id]  # type: ignore[index]
        playbook_id = self.playbooks[unit.ref]  # type: ignore[index]
        version_id = db.get(Playbook, playbook_id).current_version_id
        rules = PlaybooksService(db)._auto_rule_payloads(
            org_id=self.org_id, playbook_version_id=version_id
        )
        raw = await ai_controller.run_structured_skill(
            db,
            skill_name="playbook_review",
            org_id=self.org_id,
            created_by_user_id=self.runner_id,
            input_payload={
                "contract_id": ing.contract_id,
                "contract_version_id": ing.version_id,
                "playbook_id": playbook_id,
                "playbook_version_id": version_id,
                "rules": rules,
            },
            request_id=unit.unit_id,
            resource_type="contract",
            resource_id=ing.contract_id,
        )
        output = PlaybookReviewOutput.model_validate(raw)
        rows = list(
            db.scalars(
                select(PlaybookRule)
                .where(
                    PlaybookRule.org_id == self.org_id,
                    PlaybookRule.playbook_version_id == version_id,
                )
                .order_by(PlaybookRule.created_at.asc())
            )
        )
        key = _review_key(self.content, unit.unit_id)
        planted = [
            (r.rule, r.planted.changed_text)
            for r in (key.rules if key else [])
            if r.planted and r.planted.changed_text
        ]
        flags = []
        for found in ai_deviations_to_evaluated(ai_output=output, rules=rows):
            rule, how = _attribute(found, rows, planted)
            flags.append(
                {
                    "rule": rule,
                    "status": "deviation" if found.original_text else "missing",
                    "severity": found.severity,
                    "quote": (found.citation or {}).get("quote") or found.original_text,
                    "attributed_by": how,
                }
            )
        return {"flags": flags}

    async def _ask(self, db: Session, unit: Unit) -> dict:
        """A question put to Contract Brain through the app's own ``ask`` route function: its
        scope check, its search over the exam database, the context that search returns (never an
        empty one, FR-24), the answer and its grounding."""
        portfolio = unit.kind == "portfolio"
        if portfolio:
            question = self.portfolio_by_id[unit.unit_id].question
            user = db.get(User, self.portfolio_id)
            body = BrainAskRequest(question=question, query_scope="portfolio")
        else:
            qa = self.qa_by_id[unit.unit_id]
            user = db.get(User, self.runner_id)
            body = BrainAskRequest(
                question=qa.question,
                query_scope="contract",
                contract_id=self.ingested[qa.doc_id].contract_id,
            )
        query = await ask_contract_brain(
            payload=body,
            request=SimpleNamespace(state=SimpleNamespace(request_id=unit.unit_id)),
            db=db,
            current_user=user,
        )
        meta = query.retrieval_metadata or {}
        if portfolio and meta.get("answer_mode") == "database_filter":
            return await self._filter_answer(db, unit, query, user)
        sources = meta.get("sources") or {}
        quotes = [c["quote"] for c in query.citations or [] if c.get("quote")]
        context_chars = len(sources_to_context(sources)) if sources else 0
        if portfolio:
            # A search answer (the question was not one the filter answers): only the best
            # passages were looked at, so the contracts it names are read from its text.
            return {
                "answer": query.answer,
                "answer_mode": meta.get("answer_mode") or "rag",
                "named_doc_ids": self._named(db, query.answer or ""),
                "citations": quotes,
                "context_chars": context_chars,
            }
        flat = [
            {"bucket": "semantic", "text": s["text"], "contract_id": s["contract_id"]}
            for s in sources.get("semantic", [])
        ]
        flat += [
            {"bucket": "clauses", "text": c["excerpt"], "contract_id": c["contract_id"]}
            for c in sources.get("clauses", [])
        ]
        flat += [
            {"bucket": "text", "text": m["excerpt"], "contract_id": t["contract_id"]}
            for t in sources.get("text", [])
            for m in t.get("matches", [])
        ]
        flat += [
            {"bucket": "graph", "text": g["fact"], "contract_id": None}
            for g in sources.get("graph", [])
        ]
        return {
            "answer": query.answer,
            "answer_mode": meta.get("answer_mode") or "rag",
            "citations": quotes,
            "sources": flat,
            "context_chars": context_chars,
        }

    async def _filter_answer(self, db: Session, unit: Unit, query, user: User) -> dict:
        """A "which contracts" question the app answered with its filter. The AI reading it queued
        for wording is run here to the end, as the worker would, and the final list is fetched the
        way the page fetches it (``get_brain_query_result``). The contracts it names are exactly
        the filter's matches: no text is read for names."""
        review_id = ((query.retrieval_metadata or {}).get("reading") or {}).get("tabular_review_id")
        query_id = query.id
        db.commit()  # nothing is held open while the AI reads
        if review_id:
            await self._read_review(unit, review_id)
        result = get_brain_query_result(query_id=query_id, db=db, current_user=user)
        meta = result["retrieval_metadata"]
        reading = meta.get("reading") or {}
        if reading and not self.meter.stopped:
            # A failed AI call is a finished cell (the ledger and the cell say so); a read that
            # never ran or was never queued is neither, and scoring the list would score a bug.
            if not reading["started"]:
                raise RuntimeError("the app could not start the AI reading")
            if reading["done"] < reading["contracts"]:
                raise RuntimeError(f"the AI read {reading['done']} of {reading['contracts']} contracts")
        by_contract = {
            self.ingested[d].contract_id: d for d in self.plan.portfolio_doc_ids if d in self.ingested
        }
        return {
            "answer": result["answer"],
            "answer_mode": "database_filter",
            "named_doc_ids": sorted(
                by_contract[m["contract_id"]]
                for m in meta["matches"]
                if m["contract_id"] in by_contract
            ),
            "checked": meta["checked"],
            "total": meta["total"],
            "unknown": len(meta["unknown"]),
            "reading_failed": reading.get("failed", 0),
            "citations": [],
            "context_chars": 0,
        }

    async def _read_review(self, unit: Unit, review_id: str) -> None:
        """Run the jobs the app queued for a table review, one after another, with the job
        handler's own entry point (``tasks._run_ai_job``): its lock, its skill, its cell writes.
        Nothing reaches a broker: the queue is the in-memory backstop ``env.isolate`` installs.
        Each job is renamed ``exam:<unit>#<job>`` first, which is how the ledger ties its calls to
        this unit (``_job_units``)."""
        with self.sessions() as db:
            jobs = list(
                db.scalars(
                    select(JobRun)
                    .where(
                        JobRun.org_id == self.org_id,
                        JobRun.job_type.in_(job_tasks._TABULAR_JOB_TYPES),
                        JobRun.metadata_json["tabular_review_id"].as_string() == review_id,
                    )
                    .order_by(JobRun.created_at, JobRun.id)
                )
            )
            for job in jobs:
                job.idempotency_key = f"exam:{unit.unit_id}#{job.id}"
            db.commit()
            job_ids = [job.id for job in jobs]
        for job_id in job_ids:
            try:
                await job_tasks._run_ai_job(job_id)
            except Exception:
                # The job row, the cell and the ledger already say why it failed, and the contract
                # is listed as one the AI could not read. The rest are still read, unless the
                # ceiling stopped the run.
                if self.meter.stopped:
                    break

    def _named(self, db: Session, answer: str) -> list[str]:
        """The portfolio documents an answer names: by the contract's title or by its counterparty.

        ponytail: a contract named in a sentence that says it does NOT qualify counts as named, and
        so does every contract that shares a named counterparty. That is the ceiling of scoring free
        text; it is now only for an answer that came from search, since the filter's matches
        (``_filter_answer``) are structured. Matching is on whole words.
        """
        text = f" {_words(answer)} "
        by_contract = {
            self.ingested[d].contract_id: d
            for d in self.plan.portfolio_doc_ids
            if d in self.ingested
        }
        named = []
        for contract in db.scalars(select(Contract).where(Contract.id.in_(list(by_contract)))):
            names = (_words(contract.title), _words(contract.counterparty_name or ""))
            if any(n and f" {n} " in text for n in names):
                named.append(by_contract[contract.id])
        return sorted(named)

    async def _route(self, db: Session, unit: Unit) -> dict:
        """Intake triage of a free-text request (a transient request, never saved), as the app
        triages one: the model reads it against today's workflows."""
        asked = self.request_by_id[unit.unit_id]
        request = IntakeRequest(
            id=str(uuid.uuid5(EXAM_NS, unit.unit_id)),
            org_id=self.org_id,
            type_label="Other",
            subject=asked.subject,
            description=asked.body,
            field_values={},
            priority="Medium",
            source=asked.channel,
        )
        result = await asyncio.to_thread(triage, db, request)
        suggestion = result.get("flow_suggestion") or {}
        return {
            "workflow_id": suggestion.get("flow_id"),
            "needs_human": bool(suggestion.get("needs_human")),
            "source": suggestion.get("source"),
            "degraded": bool(result.get("degraded")),
        }

    # --- the exam database's ledger ---------------------------------------------------------

    async def _sync_ledger(self) -> None:
        """Make new call records visible in the live ledger (stamped as this run's), and forget the
        ones the ceiling refused. Serialised: the stamp tags every record that has no run yet."""
        async with self._lock:
            await asyncio.to_thread(self._drop_refused_rows)
            await asyncio.to_thread(
                stamp_and_mirror,
                run_id=self.run_id,
                exam_sessions=self.sessions,
                live=self.live,
                owner=self.owner,
            )

    def _drop_refused_rows(self) -> None:
        """A call the ceiling refused never left the machine: it is a unit that was not attempted,
        not a failed call. The gateway wrote a failure row for it anyway; remove it so it is not
        reported (or billed to the usage page) as a call."""
        with self.sessions() as db:
            rows = db.execute(
                select(
                    AICallLog.id, AICallLog.job_id, AICallLog.request_id, AICallLog.resource_id
                ).where(AICallLog.error_class == "CeilingReached")
            ).all()
            if not rows:
                return
            jobs = self._job_units(db)
            for row in rows:
                unit = self._unit_of(row.job_id, row.request_id, row.resource_id, jobs)
                if unit:
                    self.refused.add(unit)
            db.execute(delete(AICallLog).where(AICallLog.id.in_([r.id for r in rows])))
            db.commit()

    @staticmethod
    def _job_units(db: Session) -> dict[str, str]:
        return {
            job_id: key.removeprefix("exam:").split("#")[0]  # a table read's jobs share its unit
            for job_id, key in db.execute(
                select(JobRun.id, JobRun.idempotency_key).where(
                    JobRun.idempotency_key.like("exam:%")
                )
            )
        }

    def _unit_of(self, job_id, request_id, resource_id, jobs: dict[str, str]) -> str | None:
        """Which unit a ledger row belongs to (plan, "Item <-> AI-call linkage")."""
        if job_id:
            return jobs.get(job_id)
        if request_id in self.units_by_id:
            return request_id
        return self.route_of.get(resource_id)

    # --- the two phases ---------------------------------------------------------------------

    async def drive(self) -> dict:
        """Phase 1, the knowledge graph, phase 2, then the record. Each step is skipped if the state
        on disk says it is done, which is all ``--resume`` is."""
        if self.state["phase"] == 1:
            if self.state["step"] != "replayed":
                await self._phase(1)
            began = time.monotonic()
            self._owner_contracts_visible(False)
            await self._brain()
            self._add_wall_time("phase_1", began)
            self._set(phase=2, step="ingested")
        if self.state["phase"] == 2:
            if self.state["step"] != "replayed":
                await self._phase(2)
            self._set(phase=3, step="replayed")
        return await self.finalize()

    async def _phase(self, p: int) -> None:
        """One dependency phase. Batch units: capture their requests, send them as one batch, wait,
        then replay. Direct units (the owner's contracts, or everything under ``sync`` delivery) run
        as normal calls while the batch is processing."""
        began = time.monotonic()
        units = self.state["units"]
        step = self.state["step"] if self.state["phase"] == p else "ingested"
        todo = [
            u
            for u in self.plan.units
            if u.phase == p and units[u.unit_id] in ("pending", "captured")
        ]
        batched = [u for u in todo if u.delivery == "batch"]
        direct = [u for u in todo if u.delivery == "sync"]
        print(f"Phase {p}: {len(batched)} batched and {len(direct)} direct calls.")
        if step in ("submitting", "submit_failed"):
            await self._resubmit(p)
        elif step in ("ingested", "captured") and batched:
            await self._capture_and_submit(p, batched)
        if direct:
            await self._run_chains(direct, "live", record=True)
        if any(
            b["phase"] == p and b["batch_id"] and not b["collected"] for b in self.state["batches"]
        ):
            await self._collect(p)
        self._set(step="replayed")
        self._add_wall_time(f"phase_{p}", began)

    async def _capture_and_submit(self, p: int, batched: list[Unit]) -> None:
        if self.meter.stopped:  # the ceiling already stopped the run: nothing more goes out
            for unit in batched:
                self._record(unit, "not_attempted")
            return
        status = await self._run_chains(batched, "capture", record=False)
        flying: dict[str, str] = {}
        for unit in batched:
            if status[unit.unit_id] == "captured":
                flying[self.cid_of[unit.unit_id]] = unit.unit_id
            else:  # done without an AI call (a count question), or failed before it
                self._record(unit, status[unit.unit_id])
        self._set(step="captured")
        await self._admit_and_submit(p, flying)

    async def _admit_and_submit(self, p: int, members: dict[str, str]) -> None:
        """Admit requests, in planned order, while their worst case fits under the ceiling (the hard
        limit of FR-32), and send those as the phase's batch. The rest are not attempted."""
        admitted = {custom_id(p_) for p_ in self.meter.admit([self.by_cid[c] for c in members])}
        for cid, unit_id in members.items():
            self._record(
                self.units_by_id[unit_id], "captured" if cid in admitted else "not_attempted"
            )
        sent = {cid: unit_id for cid, unit_id in members.items() if cid in admitted}
        if sent:
            await self._submit(p, sent)

    async def _submit(
        self, p: int, members: dict[str, str], *, retry_of: str | None = None
    ) -> None:
        """Create the batch. The state file says ``submitting`` BEFORE the call and ``submitted``
        (with the batch id) after: a process that dies in between leaves the one state in which a
        billed batch may exist that nobody has the id of."""
        batch = {
            "phase": p,
            "batch_id": None,
            "retry_of": retry_of,
            "submitted_at": None,
            "ended_at": None,
            "collected": False,
            "counts": {},
            "usd": 0.0,
            "requests": {
                cid: {"unit_id": uid, "worst_usd": worst_cost(self.by_cid[cid], batched=True)}
                for cid, uid in members.items()
            },
        }
        self._write_requests(p, members)
        self.state["batches"].append(batch)
        self._set(step="submitting")
        try:
            batch["batch_id"] = await claude.claude_client.create_batch(
                [{"custom_id": cid, "params": self.by_cid[cid]} for cid in members]
            )
        except Exception as exc:
            # Not retried here: a lost reply to a create that did succeed would bill a second batch.
            self._set(step="submit_failed")
            raise RunPaused(
                f"the batch for phase {p} could not be created ({type(exc).__name__}). It may "
                "still exist and be billed: check the Anthropic console, then run again with "
                "--resume (you will be asked before anything is sent again) or --abandon it."
            ) from exc
        batch["submitted_at"] = _now()
        self._set(step="retry_submitted" if retry_of else "submitted")
        print(f"  Phase {p}: batch {batch['batch_id']} sent with {len(members)} requests.")

    async def _resubmit(self, p: int) -> None:
        """Resume after a create call that may or may not have succeeded. Only the owner can know
        (the console shows batches); asking is the only safe default."""
        lost = [b for b in self.state["batches"] if b["phase"] == p and b["batch_id"] is None]
        answer = self.ask(
            f"A batch for phase {p} may have been created without a reply — check the Anthropic "
            "console. Submit again? [y/N] "
        )
        if answer.strip().lower() != "y":
            raise RunPaused(
                f"phase {p}'s batch was not sent again; the run is still waiting. Use --resume to "
                "be asked again, or --abandon to finish it as incomplete."
            )
        members: dict[str, str] = {}
        for batch in lost:
            # If it was created it is billed: keep its worst case held so the ceiling stays a limit.
            self.meter.reserved_usd += sum(v["worst_usd"] for v in batch["requests"].values())
            batch["collected"] = True  # never collected: replaced by the new one
            members.update({cid: v["unit_id"] for cid, v in batch["requests"].items()})
        self._read_requests(p)
        await self._admit_and_submit(p, members)

    async def _collect(self, p: int) -> None:
        """Wait for the phase's batch(es), retry transient errors once, settle the spend, replay."""
        results: dict[str, dict] = {}
        batch_of: dict[str, str] = {}
        seen: set[str] = set()
        while True:
            batch = next(
                (
                    b
                    for b in self.state["batches"]
                    if b["phase"] == p and b["batch_id"] and b["batch_id"] not in seen
                ),
                None,
            )
            if batch is None:
                break
            seen.add(batch["batch_id"])
            got = await self._finish_batch(batch)
            results.update(got)
            batch_of.update(dict.fromkeys(got, batch["batch_id"]))
            self._settle(batch, got)
            retried = any(b["retry_of"] for b in self.state["batches"] if b["phase"] == p)
            if batch["retry_of"] is None and not retried:
                await self._retry_transient(p, batch, got)
        replay = [
            u
            for u in self.plan.units
            if u.phase == p
            and u.delivery == "batch"
            and self.state["units"][u.unit_id] == "captured"
        ]
        self.meter.results, self.meter.batch_id_of = results, batch_of
        await self._run_chains(replay, "replay", record=True)
        for b in self.state["batches"]:
            if b["phase"] == p:
                b["collected"] = True
        self._save()

    async def _finish_batch(self, batch: dict) -> dict[str, dict]:
        """Poll until the provider says the batch has ended (it ends one at 24 hours at the latest,
        expiring what is left), then fetch its results."""
        while True:
            info = await claude.claude_client.batch_status(batch["batch_id"])
            if info.get("processing_status") == "ended":
                break
            counts = ", ".join(
                f"{n} {k}" for k, n in (info.get("request_counts") or {}).items() if n
            )
            print(f"  Phase {batch['phase']}: waiting for batch {batch['batch_id']} ({counts})")
            await asyncio.sleep(POLL_SECONDS)
        got = await claude.claude_client.batch_results(batch["batch_id"])
        ended = info.get("ended_at")  # the SDK's to_dict() keeps a datetime as a datetime
        batch["ended_at"] = ended.isoformat() if isinstance(ended, datetime) else ended or _now()
        batch["counts"] = dict(Counter(r.get("type") for r in got.values()))
        return got

    def _settle(self, batch: dict, got: dict[str, dict]) -> None:
        """Swap the batch's worst-case holds for what its answers cost (half price). Units already
        replayed (a resumed run) are in the ledger's spend already and are left out of the meter;
        the batch's own figure covers every answer it returned, so settling it again after a
        resume (a retry batch was out when the process died) gives the same number, not twice it."""
        open_ = [
            cid
            for cid, v in batch["requests"].items()
            if self.state["units"][v["unit_id"]] == "captured"
        ]
        self.meter.settle_batch([self.by_cid[cid] for cid in open_], got)
        batch["usd"] = round(
            sum(
                _message_cost(self.by_cid[cid], got[cid]["message"], batched=True)
                for cid in batch["requests"]
                if (got.get(cid) or {}).get("type") == "succeeded"
            ),
            6,
        )

    async def _retry_transient(self, p: int, batch: dict, got: dict[str, dict]) -> None:
        """One retry batch for requests that ended in a transient error, under the same admission
        rule. A request not admitted keeps its error and is a failed call (FR-40)."""
        again = {
            cid: v["unit_id"]
            for cid, v in batch["requests"].items()
            if _transient(got.get(cid)) and self.state["units"][v["unit_id"]] == "captured"
        }
        admitted = {custom_id(p_) for p_ in self.meter.admit([self.by_cid[c] for c in again])}
        members = {cid: uid for cid, uid in again.items() if cid in admitted}
        if members:
            await self._submit(p, members, retry_of=batch["batch_id"])

    def _owner_contracts_visible(self, visible: bool) -> None:
        """Take the owner's contracts out of the app's reach while phase 2 runs (soft delete), and
        put them back for the record.

        Phase 2 sends public and constructed questions in a batch, and the app's search looks past
        the contract it is asked about: "this clause also appears in <other contract's title>"
        (``clause_language_matches``) names any contract the asking user may open -- the runner
        is an administrator, so that includes the owner's. A title is a client's name. The owner's
        contracts are finished with by now (their calls are phase 1), so the app is told they are
        deleted; the rows stay for the extraction read, and are restored before the record is
        built.
        """
        ids = [
            ing.contract_id
            for doc_id, ing in self.ingested.items()
            if self.docs_by_id[doc_id].category is SourceCategory.OWNER
        ]
        if not ids:
            return
        with self.sessions() as db:
            db.execute(
                update(Contract)
                .where(Contract.id.in_(ids))
                .values(deleted_at=None if visible else datetime.now(UTC))
            )
            db.commit()

    async def _brain(self) -> None:
        """Between the phases: the knowledge-graph slice of every document that has one, built from
        what phase 1 stored (free; the Contract Brain job's own step)."""
        for doc_id in self.plan.doc_ids:
            doc = self.docs_by_id[doc_id]
            if (
                "brain" not in doc.ai_steps
                or doc_id not in self.ingested
                or doc_id in self.doc_failure
            ):
                continue
            try:
                await asyncio.to_thread(self._brain_one, doc_id)
            except Exception as exc:
                why = f"brain ingestion failed: {type(exc).__name__}: {str(exc)[:200]}"
                self.state["brain_failures"][doc_id] = why
        # An answer from a search over a graph that lacks a document would be scored against an exam
        # the app did not take: those questions fail here, unasked, instead of spending money.
        failures = self.state["brain_failures"]
        for unit in self.plan.units:
            if unit.phase != 2 or unit.kind not in ("qa", "portfolio"):
                continue
            broken = next((d for d in self.item_docs[unit.unit_id] if d in failures), None)
            if broken and self.state["units"][unit.unit_id] == "pending":
                self.unit_failure[unit.unit_id] = f"upstream: {failures[broken]}"
                self._record(unit, "failed")
        self._save()

    def _brain_one(self, doc_id: str) -> None:
        ing = self.ingested[doc_id]
        uploader = (
            self.portfolio_id if self.docs_by_id[doc_id].uploader == "portfolio" else self.runner_id
        )
        with self.sessions() as db:
            ingest_contract_brain(
                db,
                org_id=self.org_id,
                created_by_user_id=uploader,
                contract=db.get(Contract, ing.contract_id),
                version=db.get(ContractVersion, ing.version_id),
                snapshot=db.get(ContractTextSnapshot, ing.snapshot_id),
            )
            db.commit()

    # --- the record -------------------------------------------------------------------------

    def _snapshot_text(self, db: Session, snapshot_id: str) -> str:
        if snapshot_id not in self._texts:
            self._texts[snapshot_id] = (
                db.scalar(
                    select(ContractTextSnapshot.text).where(ContractTextSnapshot.id == snapshot_id)
                )
                or ""
            )
        return self._texts[snapshot_id]

    def _calls(self, db: Session) -> tuple[list[dict], dict[str, list[dict]]]:
        """Every AI call of the run from the exam database's ledger, priced at what was paid
        (batch rows halved), with how much of the document it saw (FR-10) and whether each quote it
        returned is in the source word for word (FR-12)."""
        from eval.exam.owner import verify_quote  # owner imports this module

        jobs = self._job_units(db)
        rows = list(
            db.scalars(
                select(AICallLog)
                .where(
                    AICallLog.eval_run_id == self.run_id,
                    or_(AICallLog.error_class.is_(None), AICallLog.error_class != "CeilingReached"),
                )
                .order_by(AICallLog.created_at, AICallLog.id)
            )
        )
        skill_runs = {
            sid: (contract_id, version_id, snapshot_id)
            for sid, contract_id, version_id, snapshot_id in db.execute(
                select(
                    AISkillRun.id,
                    AISkillRun.contract_id,
                    AISkillRun.contract_version_id,
                    AISkillRun.text_snapshot_id,
                ).where(AISkillRun.id.in_([r.skill_run_id for r in rows if r.skill_run_id]))
            )
        }
        dependents: dict[str, list[str]] = defaultdict(list)
        for item in self.plan.items:
            for doc_id in item.docs:
                dependents[doc_id].append(item.item_id)
        seen_cache: dict[tuple[str, str], int] = {}
        calls: list[dict] = []
        by_unit: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            unit_id = self._unit_of(row.job_id, row.request_id, row.resource_id, jobs)
            unit = self.units_by_id.get(unit_id)
            doc_id = unit.doc_id if unit else None
            call_type = row.prompt_key
            seen = total = None
            run = skill_runs.get(row.skill_run_id)
            if (
                run
                and run[2]
                and call_type
                in (
                    "clause_labeling",
                    "clause_extraction",
                    "contract_metadata_extraction",
                    "playbook_review",
                )
            ):
                total = len(self._snapshot_text(db, run[2]))
                if (call_type, run[2]) not in seen_cache:
                    seen_cache[(call_type, run[2])] = self._chars_seen(db, call_type, run)
                seen = seen_cache[(call_type, run[2])]
            where = (
                list(self.plan.portfolio_doc_ids) if unit and unit.kind == "portfolio" else [doc_id]
            )
            texts = [
                self._snapshot_text(db, self.ingested[d].snapshot_id)
                for d in where
                if d in self.ingested
            ]
            quotes = []
            for quote in self._quotes(row):
                exact = any(verify_quote(quote, t) for t in texts)
                aligned = (
                    100.0
                    if exact
                    else max((align_citation_to_source(quote, t)[1] for t in texts), default=0.0)
                )
                quotes.append({"text": quote, "word_for_word": exact, "align_score": aligned})
            batched = bool(row.batched)
            tokens = (
                row.prompt_tokens or 0,
                row.completion_tokens or 0,
                row.model,
                row.cache_read_input_tokens or 0,
                row.cache_creation_input_tokens or 0,
            )
            call = {
                "call_id": row.id,
                "item_ids": [unit_id]
                if unit and unit.kind not in ("clause", "metadata")
                else dependents.get(doc_id, []),
                "doc_id": doc_id,
                "call_type": call_type,
                "batched": batched,
                "model": row.model,
                "status": row.status,
                "stop_reason": row.stop_reason,
                "prompt_tokens": tokens[0],
                "completion_tokens": tokens[1],
                "cache_read_tokens": tokens[3],
                "cache_write_tokens": tokens[4],
                "latency_ms": None if batched or row.latency_ms is None else round(row.latency_ms),
                "cost_usd": token_cost(*tokens, batched=batched),
                "chars_seen": seen,
                "chars_total": total,
                "quotes": quotes,
            }
            calls.append(call)
            if unit_id:
                by_unit[unit_id].append(call)
        return calls, by_unit

    def _chars_seen(self, db: Session, call_type: str, run: tuple) -> int:
        """How much of the document the call's prompt carried: the context the app builds for the
        skill (recomputed, deterministic), or -- for labelling, which sends excerpts -- their
        length."""
        if call_type == "clause_labeling":
            return sum(len(s.excerpt) for s in segments_for_snapshot(db, run[2]))
        return len(
            build_contract_context(
                db,
                user=None,
                org_id=self.org_id,
                contract_id=run[0],
                contract_version_id=run[1],
                text_snapshot_id=run[2],
            ).text
        )

    @staticmethod
    def _quotes(row: AICallLog) -> list[str]:
        """The passages of the contract an answer quotes: its citations, and a deviation's
        ``original_text``. Only a call that gave an answer has any."""
        out = row.validated_output if row.status == "succeeded" else None
        out = out or {}
        quotes = [c["quote"] for c in out.get("citations") or [] if c.get("quote")]
        for deviation in out.get("deviations") or []:
            quotes += [c["quote"] for c in deviation.get("citations") or [] if c.get("quote")]
            if deviation.get("original_text"):
                quotes.append(deviation["original_text"])
        return quotes

    def _read_extraction(self, db: Session, ing: evaldb.IngestedDoc) -> dict:
        """What the app extracted from a document, as the exam's extraction prediction: the contract
        row its metadata skill filled, and the clauses its labelling skill stored."""
        contract = db.get(Contract, ing.contract_id)
        clauses: dict[str, list[str]] = defaultdict(list)
        for kind, text in db.execute(
            select(ClauseExtraction.clause_type, ClauseExtraction.text)
            .where(
                ClauseExtraction.contract_id == ing.contract_id,
                ClauseExtraction.contract_version_id == ing.version_id,
                ClauseExtraction.is_stale.is_(False),
            )
            .order_by(ClauseExtraction.start_char)
        ):
            clauses[kind].append(text)
        ends = contract.expiration_date.isoformat() if contract.expiration_date else None
        value = (
            {"amount": f"{contract.value_amount:.2f}", "currency": contract.currency}
            if contract.value_amount is not None
            else None
        )
        return {
            "fields": {
                # ponytail: the parties are the contract's counterparty text split like the scorer
                # splits a key; one field cannot say which of two named parties is "ours".
                "parties": score._split_parties(contract.counterparty_name),
                "effective_date": contract.effective_date.isoformat()
                if contract.effective_date
                else None,
                "expiration_date": ends,
                "end_date_or_term": ends,
                "governing_law": contract.jurisdiction,
                "value": value,
                **{
                    name: [t for k in kinds for t in clauses.get(k, [])] or None
                    for name, kinds in _CLAUSE_FIELDS.items()
                },
            }
        }

    def _item(
        self, item: Item, unit_calls: dict[str, list[dict]], extraction: dict[str, dict]
    ) -> dict:
        """One item's status. Read from the LEDGER, not from exceptions: the app swallows many
        provider failures into fallbacks, so a unit can "succeed" with a failed call behind it."""
        states = self.state["units"]
        upstream = [u for d in item.docs for u in self.doc_units.get(d, [])]
        every = [*item.own, *upstream]
        calls = [c for u in every for c in unit_calls.get(u, [])]
        base = {
            "item_id": item.item_id,
            "task": item.task,
            "doc_id": item.doc_id,
            "call_ids": [c["call_id"] for c in calls],
            "prediction": None,
        }

        def failed(reason: str, call_id: str | None = None) -> dict:
            failure = {"call_id": call_id, "reason": reason} if call_id else {"reason": reason}
            return {**base, "status": "failed", "failure": failure}

        broken = next((d for d in item.docs if d in self.doc_failure), None)
        if broken:
            return failed(f"upstream: {self.doc_failure[broken]}")
        if any(states.get(u) in (None, "pending", "captured", "not_attempted") for u in every):
            reason = "not attempted: the spending ceiling stopped the run"
            return {**base, "status": "not_attempted", "failure": {"reason": reason}}
        # The failed call is a more useful reason than the exception it raised out of the app.
        bad = next(
            (c for u in item.own for c in unit_calls.get(u, []) if c["status"] != "succeeded"), None
        )
        if bad:
            return failed(f"call {bad['status']}", bad["call_id"])
        if own := next((u for u in item.own if states[u] == "failed"), None):
            return failed(self.unit_failure.get(own) or "unit failed")
        if up := next((u for u in upstream if states[u] == "failed"), None):
            return failed(f"upstream: {self.unit_failure.get(up) or 'unit failed'}")
        bad = next(
            (c for u in upstream for c in unit_calls.get(u, []) if c["status"] != "succeeded"), None
        )
        if bad:
            return failed(f"upstream: {bad['call_type']} call {bad['status']} for {bad['doc_id']}")
        prediction = (
            extraction.get(item.item_id)
            if item.task == "extraction"
            else self.predictions.get(item.item_id)
        )
        if prediction is None:
            return failed("no answer was produced")
        return {**base, "status": "scored", "failure": None, "prediction": prediction}

    async def finalize(self) -> dict:
        """Write the run's record: results (read-only, hashed), the owner's detail file, and the
        immutable ``eval.run_finished`` audit row. Then the meter is released."""
        await self._sync_ledger()
        self._owner_contracts_visible(True)  # the context rebuilds below need them back
        with self.sessions() as db:
            calls, unit_calls = self._calls(db)
            extraction = {
                i.item_id: self._read_extraction(db, self.ingested[i.doc_id])
                for i in self.plan.items
                if i.task == Task.EXTRACTION.value and i.doc_id in self.ingested
            }
            versions = self._versions(db)
        items = [self._item(i, unit_calls, extraction) for i in self.plan.items]
        failed_calls = sum(c["status"] != "succeeded" for c in calls)
        ceiling = self.meter.stopped or any(i["status"] == "not_attempted" for i in items)
        outcome, reason = ("incomplete", "ceiling_reached") if ceiling else ("complete", None)
        finished_at = _now()
        paid = self.paid
        results = {
            "schema": "aegis-exam-results/1",
            "run_id": paid.run_id,
            "kind": "exam",
            "mode": self.plan.mode,
            "delivery": self.plan.delivery,
            "change": self.change,
            "target_area": self.target,
            "started_at": self.state["started_at"],
            "finished_at": finished_at,
            "outcome": outcome,
            "incomplete_reason": reason,
            "contains_errors": bool(
                failed_calls
                or self.doc_failure
                or self.state["brain_failures"]
                or any(i["status"] == "failed" for i in items)
            ),
            "failed_call_rate": failed_calls / len(calls) if calls else 0.0,
            "versions": versions,
            "estimate": asdict(paid.estimate),
            "ceiling_usd": paid.ceiling_usd,
            "approved_by": _person(paid.owner),
            "excluded": self.plan.excluded,
            "batches": [
                {
                    "phase": b["phase"],
                    "batch_id": b["batch_id"],
                    "retry_of": b["retry_of"],
                    "requests": len(b["requests"]),
                    "admitted": len(b["requests"]),
                    **{
                        k: b["counts"].get(k, 0)
                        for k in ("succeeded", "errored", "expired", "canceled")
                    },
                    "submitted_at": b["submitted_at"],
                    "ended_at": b["ended_at"],
                    "usd": b["usd"],
                }
                for b in self.state["batches"]
                if b["batch_id"]
            ],
            "documents": [self._document_record(d) for d in self.plan.doc_ids],
            "items": items,
            "calls": calls,
            "phase_wall_time_s": self.state["phase_wall_time_s"],
            "wall_time_s": (
                datetime.fromisoformat(finished_at)
                - datetime.fromisoformat(self.state["started_at"])
            ).total_seconds(),
        }
        path = self.dir / "results.json"  # type: ignore[operator]
        path.unlink(missing_ok=True)  # a read-only file from an earlier, interrupted finish
        path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        os.chmod(path, 0o444)  # written once; the audit row pins its hash
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        headline: dict = {}
        try:
            scores = score.score_run(results, self.content)
            detail = self.dir / "detail.md"  # type: ignore[operator]
            report.write_detail(results, scores, detail, content=self.content)
            headline = {
                k: m["value"] for k, m in scores["measures"].items() if m["role"] == score.HEADLINE
            }
        except Exception as exc:
            # The record above is what matters; a scoring bug must not leave the run unfinished.
            print(f"WARNING: the run was recorded but could not be scored ({type(exc).__name__})")
            print(f"  {exc}")
        actual = ledger_actual(self.live.sessions, paid.run_id, org_id=self.live.org_id)
        finish_paid_run(
            paid,
            outcome=outcome,
            reason=reason,
            actual=actual,
            results_sha256=sha,
            summary={
                "mode": self.plan.mode,
                "change": self.change,
                "target_area": self.target,
                "versions": versions,
                "contains_errors": results["contains_errors"],
                "failed_call_rate": results["failed_call_rate"],
                "batches": [
                    {"phase": b["phase"], "batch_id": b["batch_id"], "counts": b["counts"]}
                    for b in self.state["batches"]
                    if b["batch_id"]
                ],
                "headline": headline,
            },
        )
        _print_summary(results, actual, paid.estimate)
        return results

    def _document_record(self, doc_id: str) -> dict:
        ing = self.ingested.get(doc_id)
        if ing is None:
            return {
                "doc_id": doc_id,
                "contract_id": None,
                "text_len": None,
                "method": None,
                "elements": None,
                "status": "failed",
                "failure": self.doc_failure.get(doc_id),
            }
        return {
            "doc_id": doc_id,
            "contract_id": ing.contract_id,
            "text_len": ing.text_len,
            "method": ing.method,
            "elements": ing.elements,
            "status": "failed" if doc_id in self.doc_failure else "ok",
            "failure": self.doc_failure.get(doc_id) or self.state["brain_failures"].get(doc_id),
        }

    def _versions(self, db: Session) -> dict:
        rows = db.execute(
            select(
                AICallLog.model,
                AICallLog.prompt_key,
                AICallLog.prompt_version,
                AICallLog.prompt_hash,
            )
            .where(AICallLog.eval_run_id == self.run_id)
            .distinct()
        ).all()
        return {
            "app_code_sha256": app_code_sha256(),
            "git_sha": os.environ.get("EXAM_GIT_SHA") or None,
            "model_under_test": settings.claude_model,
            "models": sorted({r.model for r in rows if r.model}),
            "prompts": sorted(
                (
                    {"prompt_key": r.prompt_key, "version": r.prompt_version, "hash": r.prompt_hash}
                    for r in rows
                    if r.prompt_key
                ),
                key=lambda p: (p["prompt_key"], p["version"], p["hash"]),
            ),
            "content_fingerprint": content_fingerprint(self.content),
        }


def _print_summary(results: dict, actual: dict, estimate: Estimate) -> None:
    flags = [f for f in report.run_flags(results) if f != "sample"]
    print(
        f"\nRun {results['run_id']} finished: {results['outcome']}"
        + (f" ({', '.join(flags)})" if flags else "")
    )
    print(f"  Cost: ${actual['usd']:,.2f} against an estimate of ${estimate.usd:,.2f}")
    for name, row in sorted(actual["by_call_type"].items()):
        print(f"    {name:<30}{row['calls']:>4} calls  ${row['usd']:,.2f}")
    for status in ("scored", "failed", "not_attempted"):
        count = sum(i["status"] == status for i in results["items"])
        print(f"  Items {status.replace('_', ' ')}: {count}")


# --- the three commands ---------------------------------------------------------------------


def _refuse_if_waiting(live: LiveDB) -> None:
    """A new run rebuilds the exam database from nothing; a run waiting on a batch needs the old
    one to replay into, so it must be resumed or abandoned first."""
    for row in _unfinished_runs(live):
        if row.after["kind"] == "exam" and _waiting_on_batch(row.resource_id):
            raise ExamRefused(
                f"run {row.resource_id} is waiting on a batch. Finish it first with --resume "
                f"{row.resource_id}, or --abandon {row.resource_id}: starting a new run would "
                "erase the database its answers go into."
            )


async def _refuse_if_ocr_missing(plan: Plan, content: ExamContent) -> None:
    """A planned scan that has not been read by OCR cannot be read at all, and the app would not
    say so (it keeps the thin native text): refuse before the owner is asked for money."""
    from eval.exam.owner import plan_ocr  # owner imports this module

    missing = [
        i for i in await plan_ocr(content, content.owner_manifest) if i.doc_id in plan.doc_ids
    ]
    if missing:
        raise ExamRefused(
            "these scans have not been read by OCR yet: "
            + ", ".join(i.doc_id for i in missing)
            + ". Run `ocr` first. Nothing was spent."
        )


def _approval(live: LiveDB, run_id: str) -> tuple[dict, datetime]:
    """The approval record of an exam run that has not finished (``--resume`` / ``--abandon``)."""
    with live.sessions() as db:
        row = db.execute(
            select(AuditLog.after, AuditLog.created_at).where(
                AuditLog.org_id == live.org_id,
                AuditLog.action == "eval.spend_approved",
                AuditLog.resource_id == run_id,
            )
        ).first()
        finished = db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.org_id == live.org_id,
                AuditLog.action == "eval.run_finished",
                AuditLog.resource_id == run_id,
            )
        )
    if row is None or row.after.get("kind") != "exam":
        raise ExamRefused(f"there is no approved exam run {run_id}")
    if finished:
        raise ExamRefused(f"run {run_id} has already finished; its record cannot be changed")
    return row.after, row.created_at


async def execute_exam_run(
    *,
    live: LiveDB,
    owner: Owner,
    mode: Literal["sample", "full"],
    delivery: Literal["batch", "sync"],
    change: str | None,
    target: str | None,
    ask: Callable[[str], str],
    eval_db_name: str = EVAL_DB_NAME,
    content: ExamContent | None = None,
) -> dict:
    """Run the exam (FR-1, FR-30..FR-33, FR-41) and return its ``results.json`` as a dict.

    Order: recover any run that crashed; plan; rebuild the throwaway database and read every
    document in; capture what the run will send and price it into the estimate (all free: no
    request can leave before the go-ahead); take the owner's go-ahead and ceiling; run the two
    phases; write the record. ``content`` (default: the exam's content on disk) lets a test run a
    small exam. Raises ``ExamRefused`` when nothing was spent and ``RunPaused`` when a batch is
    waiting for ``--resume``.
    """
    if mode not in ("sample", "full") or delivery not in ("batch", "sync"):
        raise ExamRefused("mode is sample or full; delivery is batch or sync")
    if (change is None) != (target is None):
        raise ExamRefused(
            "--change and --target go together: name the change and the area it targets"
        )
    if target is not None and target not in score.MAIN_MEASURE:
        raise ExamRefused(f"--target is one of: {', '.join(score.MAIN_MEASURE)}")
    from eval.exam import owner as owner_mod  # owner and routing import this module
    from eval.exam import routing

    with exam_lock(live):
        _refuse_if_waiting(live)
        reconcile_crashed_runs(live, exam_sessions=_exam_sessions())
        content = content if content is not None else load_content()
        stale = owner_mod.stale_keys(content.owner_keys, content.owner_manifest)
        fresh = (
            routing.routing_fresh(live, content.workflow_snapshot) if content.routing_keys else True
        )
        plan = plan_run(content, mode, delivery, stale_owner_keys=stale, routing_current=fresh)
        if not plan.items:
            raise ExamRefused(
                "there is nothing to run: the exam content has no items for this mode"
            )
        await _refuse_if_ocr_missing(plan, content)
        _rebuild_exam_db(live, eval_db_name)
        run = _Run(
            live=live,
            owner=owner,
            content=content,
            plan=plan,
            sessions=_exam_sessions(),
            ask=ask,
            change=change,
            target=target,
        )
        print("Reading the exam's documents into the throwaway database (free)...")
        await run.prepare()
        await run.preview()
        calibrated_from, per_call = _calibration(live, mode, delivery)
        if calibrated_from:
            print(f"Expected cost uses what run {calibrated_from[:8]} cost per call (calibrated).")
        estimate = build_estimate(
            run.plan,
            run.payloads,
            model=settings.claude_model,
            call_types=run.call_type,
            per_call=per_call,
            reads=run.expected_reads(),
        )
        paid = approve_paid_run(
            kind="exam",
            live=live,
            owner=owner,
            estimate=estimate,
            ask=ask,
            extra={
                "mode": mode,
                "change": change,
                "target_area": target,
                "main_measure": score.MAIN_MEASURE.get(target) if target else None,
                "versions_at_start": {
                    "app_code_sha256": app_code_sha256(),
                    "model_under_test": settings.claude_model,
                    "content_fingerprint": content_fingerprint(content),
                },
            },
        )
        if paid is None:
            raise ExamRefused("the go-ahead was not given: nothing was spent")
        run.start(paid)
        try:
            return await run.drive()
        finally:
            if claude.exam_meter is paid.meter:
                claude.exam_meter = (
                    None  # the meter must not outlive the process step it was approved for
                )


async def resume_exam_run(
    *,
    live: LiveDB,
    owner: Owner,
    run_id: str,
    ask: Callable[[str], str] | None = None,
    content: ExamContent | None = None,
) -> dict:
    """Continue a run whose process stopped, under the ceiling the owner already approved.

    Refused when the run has finished, has no saved state, its exam database is gone, or the app
    code or exam content is no longer what the run started with (then: ``--abandon``). A pending
    batch is polled and replayed; units already finished are never run again. A batch whose
    ``create`` call died or failed ("submitting" / "submit_failed") is the one state where money
    may have been spent on a batch nobody has the id of: the owner is asked, and the ceiling keeps
    that batch's worst case held either way.
    """
    ask = ask or input
    with exam_lock(live):
        approved, started = _approval(live, run_id)
        folder = RUNS_DIR / run_id
        if not (folder / "state.json").is_file() or not (folder / "plan.json").is_file():
            raise ExamRefused(f"run {run_id} has no saved state to resume from; use --abandon")
        sessions = _exam_sessions()
        try:
            with sessions() as db:
                db.scalar(select(func.count()).select_from(Contract))
        except (OperationalError, ProgrammingError) as exc:
            raise ExamRefused(
                f"the exam database of run {run_id} is gone, so it cannot be resumed; use --abandon"
            ) from exc
        content = content if content is not None else load_content()
        at_start = approved.get("versions_at_start") or {}
        if at_start.get("app_code_sha256") != app_code_sha256() or at_start.get(
            "content_fingerprint"
        ) != content_fingerprint(content):
            raise ExamRefused(
                "the app code or the exam content changed since this run started, so its answers "
                "would not belong together; use --abandon"
            )
        approver = Owner(**approved["approver"])
        spent = ledger_actual(live.sessions, run_id, org_id=live.org_id)["usd"]
        paid = PaidRun(
            run_id=run_id,
            kind="exam",
            owner=approver,
            live=live,
            estimate=Estimate(**approved["estimate"]),
            ceiling_usd=approved["ceiling_usd"],
            ceiling_credits=None,
            meter=Meter(approved["ceiling_usd"], spent_usd=spent),
            started_at=started,
        )
        run = _Run.load(
            live=live, owner=approver, content=content, paid=paid, sessions=sessions, ask=ask
        )
        audit(
            live,
            action="eval.run_resumed",
            actor=owner,
            resource_type="eval_run",
            resource_id=run_id,
            after={
                "resumed_by": _person(owner),
                "phase": run.state["phase"],
                "step": run.state["step"],
            },
        )
        claude.exam_meter = paid.meter
        try:
            return await run.drive()
        finally:
            if claude.exam_meter is paid.meter:
                claude.exam_meter = None


async def _cancel_and_price(state: dict) -> tuple[int, float, float]:
    """Cancel an abandoned run's outstanding batch and price what it had already answered.

    A cancelled batch still bills the requests it finished first. Those answers were never
    replayed, so they are in no ledger row, and the run's record would understate what the
    provider charged (FR-33, FR-39) without this. Returns (answers, usd paid, usd at list price).
    A batch that never reported its id (the create call may have succeeded) can only be cancelled
    by the owner in the console; one that cannot be read is said so, and the abandon still ends.
    """
    answers, paid, list_price = 0, 0.0, 0.0
    for batch in state.get("batches", []):
        if batch["phase"] != state["phase"] or batch.get("collected"):
            continue
        if not batch["batch_id"]:
            print(
                "A batch may have been created whose id was never received: check the "
                "Anthropic console and cancel it there."
            )
            continue
        await claude.claude_client.cancel_batch(batch["batch_id"])
        print(f"Cancelled batch {batch['batch_id']}.")
        try:
            for _ in range(_CANCEL_POLLS):  # cancelling ends the batch within about a minute
                info = await claude.claude_client.batch_status(batch["batch_id"])
                if info.get("processing_status") == "ended":
                    break
                await asyncio.sleep(POLL_SECONDS)
            else:
                raise TimeoutError("the batch had not ended")
            got = await claude.claude_client.batch_results(batch["batch_id"])
        except Exception as exc:
            print(
                f"WARNING: could not read what batch {batch['batch_id']} had already answered "
                f"({type(exc).__name__}); the cost recorded for this run may be too low."
            )
            continue
        for cid, member in batch["requests"].items():
            result = got.get(cid) or {}
            # A unit already replayed is in the ledger; only answers nobody has seen are added.
            if (
                result.get("type") == "succeeded"
                and state["units"][member["unit_id"]] == "captured"
            ):
                answers += 1
                paid += _message_cost({}, result["message"], batched=True)
                list_price += _message_cost({}, result["message"], batched=False)
    return answers, paid, list_price


async def abandon_exam_run(*, live: LiveDB, owner: Owner, run_id: str) -> None:
    """Give up on a run: cancel a batch that is still pending, copy what was spent to the live
    ledger, and finish the run ``incomplete`` (reason ``abandoned``) so it can never be part of a
    baseline. What a cancelled batch had already answered is billed, so it is added to the cost
    on record."""
    with exam_lock(live):
        approved, started = _approval(live, run_id)
        state_path = RUNS_DIR / run_id / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        waiting = state.get("step") in _WAITING_STEPS
        answers, usd, list_usd = await _cancel_and_price(state) if waiting else (0, 0.0, 0.0)
        try:
            stamp_and_mirror(
                run_id=run_id,
                exam_sessions=_exam_sessions(),
                live=live,
                owner=Owner(**approved["approver"]),
            )
        except (OperationalError, ProgrammingError):
            pass  # the exam database is gone: nothing in it to copy
        actual = ledger_actual(live.sessions, run_id, org_id=live.org_id)
        if answers:
            # ponytail: these answers have no ledger row (the app never saw them), so the live AI
            # Usage page does not show them; this record does. Replaying them would need the whole
            # run rebuilt, which is what abandoning declines to do.
            actual["usd"] = round(actual["usd"] + usd, 6)
            actual["list_usd"] = round(actual["list_usd"] + list_usd, 6)
            actual["calls"] += answers
            actual["by_call_type"]["unreplayed_batch_answers"] = {
                "calls": answers,
                "batch": answers,
                "usd": round(usd, 6),
            }
            print(f"The cancelled batch had already answered {answers} requests (${usd:,.2f}).")
        run = PaidRun(
            run_id=run_id,
            kind="exam",
            owner=owner,
            live=live,
            estimate=Estimate(**approved["estimate"]),
            ceiling_usd=approved.get("ceiling_usd"),
            ceiling_credits=None,
            meter=None,
            started_at=started,
        )
        finish_paid_run(
            run,
            outcome="incomplete",
            reason="abandoned",
            actual=actual,
            results_sha256=None,
            summary={
                "mode": approved.get("mode"),
                "change": approved.get("change"),
                "target_area": approved.get("target_area"),
                "versions": approved.get("versions_at_start"),
                "contains_errors": actual["failed_calls"] > 0,
                "failed_call_rate": actual["failed_calls"] / actual["calls"]
                if actual["calls"]
                else 0.0,
            },
        )
        print(f"Run {run_id} abandoned: recorded as incomplete.")
