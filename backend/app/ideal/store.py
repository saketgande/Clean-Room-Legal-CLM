"""Persistence by event sourcing: the events ARE the run.

Nothing stores "REQ-4123 is at Legal review with P. Nair". It stores the ordered
list of things that happened, and the current state is what you get by folding
them. That inversion buys three things a status column cannot:

  * **The audit trail is the data**, not a parallel log that can disagree with it.
  * **Any past moment is reconstructible** — replay to sequence 12 and you have
    the run exactly as it stood. For a regulated business, "what did this look
    like when it was approved?" is a real question with a real answer.
  * **Corrections are additive.** You append a correcting event; you never
    rewrite history. Nothing that has happened can be silently un-happened.

Deliberately storage-agnostic. Events go in and out as plain dicts, so the
backend is a choice: a table, JSONB, a file, or the in-memory store used in
tests. ``SqlEventStore`` is left for the moment you deploy — binding a table
here would put a prototype into the live schema and alembic chain, which is not
a decision this module should make for you.

The one rule that keeps it honest: **everything needed to rebuild a run must
live in the events.** That is why ``start()`` emits a ``started`` event carrying
the workflow key and the matter's facts, and why reaching the end emits
``finished`` rather than just setting a field.
"""

from __future__ import annotations

from dataclasses import asdict, replace

from app.ideal.workflow import (
    APPROVE,
    DONE,
    NEED_INFO,
    PARKED,
    PAUSED,
    REJECT,
    REJECTED,
    RUNNING,
    Event,
    RunState,
    Workflow,
    WorkflowError,
)

# Event kinds that change where a run IS, as opposed to merely recording
# something about it. Kept explicit so a new kind cannot silently affect state.
_STATEFUL = {"started", "entered", "assigned", "reassigned", "decided", "parked", "finished"}


# --- rows in, rows out -----------------------------------------------------

def to_row(run_id: str, event: Event) -> dict:
    """One event as a plain dict — a table row, a JSON line, whatever you like."""
    row = asdict(event)
    row["run_id"] = run_id
    return row


def from_row(row: dict) -> Event:
    known = {f for f in Event.__dataclass_fields__}
    return Event(**{k: v for k, v in row.items() if k in known})


def dump(run_id: str, state: RunState) -> list[dict]:
    """The whole run, ready to persist."""
    return [to_row(run_id, e) for e in state.events]


# --- rebuilding ------------------------------------------------------------

def replay(rows, wf: Workflow, *, upto: int | None = None) -> RunState:
    """Fold events back into a RunState.

    Note what this does NOT do: it does not re-run the routing logic. Each event
    already records what happened, so replay just applies those facts. That
    matters — it means a run rebuilds correctly even if the workflow definition
    has since been edited, which it will be.

    ``upto`` stops at a sequence number, giving you the run as it stood at any
    earlier moment.
    """
    events = sorted((from_row(r) if isinstance(r, dict) else r for r in rows),
                    key=lambda e: e.seq)
    if upto is not None:
        events = [e for e in events if e.seq <= upto]
    if not events:
        raise WorkflowError("cannot rebuild a run from no events")
    if events[0].kind != "started":
        raise WorkflowError("the first event must be 'started' — the log is incomplete")

    first = events[0]
    payload = first.payload or {}
    state = RunState(
        workflow_key=payload.get("workflow_key", wf.key),
        active=(),
        context=dict(payload.get("context") or {}),
        events=tuple(events),
    )

    active: list[str] = []
    rounds: dict[str, int] = {}
    assignees: dict[str, str] = {}
    status = RUNNING

    for ev in events:
        if ev.kind == "entered":
            # A parallel step is a container: entering it opens its CHILDREN.
            # The container itself is never work anyone holds, so it must not
            # land in `active` — otherwise a rebuilt run shows a step nobody
            # can answer and the group never appears to complete.
            if not wf.step(ev.step_id).is_parallel and ev.step_id not in active:
                active.append(ev.step_id)
            rounds[ev.step_id] = max(rounds.get(ev.step_id, 0), ev.round)
            if status == PAUSED:
                status = RUNNING
        elif ev.kind in ("assigned", "reassigned"):
            # `comment` carries the person — see workflow.assign()
            if ev.comment:
                assignees[ev.step_id] = ev.comment
        elif ev.kind == "decided":
            if ev.outcome == NEED_INFO:
                status = PAUSED                      # stays open, on their desk
            elif ev.outcome == REJECT:
                active, status = [], REJECTED
            elif ev.step_id in active:
                active.remove(ev.step_id)
        elif ev.kind == "deviated" and ev.outcome == "skip":
            if ev.step_id in active:
                active.remove(ev.step_id)
        elif ev.kind == "parked":
            status = PARKED
        elif ev.kind == "finished":
            active, status = [], DONE
        # not_applicable / changed record facts without moving the run

    return replace(
        state,
        active=tuple(active),
        rounds=rounds,
        assignees=assignees,
        status=status,
    )


# --- backends --------------------------------------------------------------

class InMemoryEventStore:
    """A dict of run_id -> rows. Enough for tests and for proving the shape.

    Append-only by construction: there is no update and no delete. A store that
    cannot rewrite history is the point.
    """

    def __init__(self) -> None:
        self._rows: dict[str, list[dict]] = {}

    def append(self, run_id: str, state: RunState) -> int:
        """Persist any events not already stored. Returns how many were new."""
        stored = self._rows.setdefault(run_id, [])
        seen = {r["seq"] for r in stored}
        fresh = [to_row(run_id, e) for e in state.events if e.seq not in seen]
        stored.extend(fresh)
        return len(fresh)

    def load(self, run_id: str, wf: Workflow, *, upto: int | None = None) -> RunState:
        rows = self._rows.get(run_id)
        if not rows:
            raise WorkflowError(f"no such run: {run_id!r}")
        return replay(rows, wf, upto=upto)

    def events(self, run_id: str) -> list[dict]:
        return list(self._rows.get(run_id, []))

    def runs(self) -> list[str]:
        return sorted(self._rows)


# --- read models -----------------------------------------------------------
#
# Replaying every run to answer "what is on my desk" would not scale, so a
# queryable projection is built from the same events. It is DERIVED — safe to
# drop and rebuild, and it can never be the thing that disagrees with history.

def inbox(store: InMemoryEventStore, wf: Workflow, person: str) -> list[dict]:
    """Everything currently sitting with one person."""
    out = []
    for run_id in store.runs():
        state = store.load(run_id, wf)
        if not state.is_open:
            continue
        for step_id, holder in state.assignees.items():
            if holder == person and step_id in state.active:
                step = wf.step(step_id)
                out.append({
                    "run_id": run_id,
                    "step": step.name,
                    "asks": step.asks,
                    "sla_hours": step.sla_hours,
                    "round": state.rounds.get(step_id, 1),
                })
    return out
