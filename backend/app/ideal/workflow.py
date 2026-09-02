"""The workflow spec and engine.

A workflow is a GRAPH. A step is a decision with typed outcomes, and each
outcome names where it goes — including backwards, which is what makes rework
expressible at all.

    Step "Legal review"
        asks: "Review the 3 flagged deviations. What do you want to do?"
        outcomes:
            approve                -> signature
            approve_with_comments  -> signature
            request_changes        -> draft        ← backwards. the send-back.
            need_info              -> PAUSE
            escalate               -> gc_review
            reject                 -> END

Everything here is pure: ``advance()`` takes a state and returns a new state.
No database, no clock, no I/O. Time is passed in when the caller has it, so the
engine stays deterministic and fully testable.

Two guards that matter in practice:

  * **Loop ceiling.** A send-back loop can cycle forever — draft, review, reject,
    draft... ``MAX_ROUNDS`` caps how many times one step may be entered and
    parks the run for a human instead of spinning.
  * **Deviation needs a reason.** Skipping a step or adding a reviewer is
    allowed — that flexibility is what keeps people inside the tool rather than
    finishing over email — but the reason is mandatory, so the departure stays
    auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# --- outcomes --------------------------------------------------------------
# The vocabulary a human (or agent) can answer a step with. The old engine had
# exactly one: "finished".
APPROVE = "approve"
APPROVE_WITH_COMMENTS = "approve_with_comments"
REQUEST_CHANGES = "request_changes"
NEED_INFO = "need_info"
ESCALATE = "escalate"
REJECT = "reject"

OUTCOMES = (
    APPROVE,
    APPROVE_WITH_COMMENTS,
    REQUEST_CHANGES,
    NEED_INFO,
    ESCALATE,
    REJECT,
)

# Reserved routing targets.
END = "END"      # terminal — the run finishes
PAUSE = "PAUSE"  # the step stays open, waiting on someone outside the flow

# A send-back cycle must not spin forever.
# ponytail: a flat per-step cap. If workflows ever need per-step ceilings,
# move this onto Step.
MAX_ROUNDS = 5

# Step kinds — who or what answers it.
HUMAN, AGENT, SYSTEM, COUNTERPARTY = "human", "agent", "system", "counterparty"


def matches(when: dict | None, context: dict) -> bool:
    """Does the matter satisfy this condition?

    A bare value means equality; a dict means a comparison. Unknown facts fail
    CLOSED — if we do not know whether the scope is GxP, Quality does not get
    silently skipped.
    """
    if not when:
        return True
    for key, expected in when.items():
        actual = context.get(key)
        if isinstance(expected, dict):
            for op, want in expected.items():
                if actual is None:
                    return False
                if op == "gte" and not actual >= want:
                    return False
                if op == "gt" and not actual > want:
                    return False
                if op == "lte" and not actual <= want:
                    return False
                if op == "lt" and not actual < want:
                    return False
                if op == "ne" and actual == want:
                    return False
                if op == "in" and actual not in want:
                    return False
        elif actual != expected:
            return False
    return True


class WorkflowError(ValueError):
    """The workflow or the move is invalid. Always a programming/config error,
    never a user-facing condition."""


# --- the spec --------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    """One decision.

    ``asks`` is not decoration. The old engine's steps carried a name and
    nothing else — "Legal Sign-off" tells the person nothing about what they are
    deciding or what their options are. Stating the question is half the fix for
    "the steps are vague".
    """

    id: str
    name: str
    asks: str
    kind: str = HUMAN
    # Whose desk this belongs on — a department or role, resolved to a
    # named person at run time. A step nobody owns is a step nobody does.
    owner: str | None = None
    outcomes: dict[str, str] = field(default_factory=dict)
    sla_hours: int | None = None
    # A parallel step opens all its children at once and completes when
    # `complete_when` is satisfied — so Finance and Quality review together
    # instead of queueing behind each other.
    parallel: tuple[str, ...] = ()
    complete_when: str = "all"  # all | any
    # Mandatory steps may only be skipped by an explicit, reasoned deviation.
    mandatory: bool = False
    # Only run this step when the matter's facts match. Declarative, not a
    # lambda, so a workflow stays inspectable and serialisable:
    #     when={"gxp": True}
    #     when={"value_inr": {"gte": 50_000_000}}
    # An NDA never needed this. An MSA cannot be expressed without it —
    # Quality reviews GxP scope only, Privacy reviews personal data only,
    # and which approver signs depends on the value.
    when: dict | None = None

    @property
    def is_parallel(self) -> bool:
        return bool(self.parallel)


@dataclass(frozen=True)
class Workflow:
    key: str
    name: str
    start: str
    steps: dict[str, Step]

    def step(self, step_id: str) -> Step:
        try:
            return self.steps[step_id]
        except KeyError:
            raise WorkflowError(f"No such step: {step_id!r}") from None


def build(key: str, name: str, start: str, steps: list[Step]) -> Workflow:
    """Assemble and validate a workflow. Validation is at build time so a broken
    definition fails on import, not halfway through a live negotiation."""
    by_id: dict[str, Step] = {}
    for s in steps:
        if s.id in by_id:
            raise WorkflowError(f"Duplicate step id: {s.id!r}")
        by_id[s.id] = s

    if start not in by_id:
        raise WorkflowError(f"start {start!r} is not a step")

    for s in by_id.values():
        if s.complete_when not in ("all", "any"):
            raise WorkflowError(f"{s.id}: complete_when must be 'all' or 'any'")
        for child in s.parallel:
            if child not in by_id:
                raise WorkflowError(f"{s.id}: parallel child {child!r} is not a step")
        for outcome, target in s.outcomes.items():
            if outcome not in OUTCOMES:
                raise WorkflowError(f"{s.id}: unknown outcome {outcome!r}")
            if target not in (END, PAUSE) and target not in by_id:
                raise WorkflowError(f"{s.id}: {outcome} routes to unknown step {target!r}")
        # A step nobody can leave is a trap.
        if not s.outcomes and not s.is_parallel:
            raise WorkflowError(f"{s.id}: has no outcomes — the run could never leave it")

    return Workflow(key=key, name=name, start=start, steps=by_id)


# --- run state -------------------------------------------------------------

RUNNING, PAUSED, DONE, REJECTED, PARKED = "running", "paused", "done", "rejected", "parked"


@dataclass(frozen=True)
class Event:
    """One immutable record of something that happened. The audit trail is the
    list of these — including every send-back and every deviation."""

    seq: int
    kind: str          # entered | decided | deviated | parked
    step_id: str
    actor: str | None = None
    outcome: str | None = None
    comment: str | None = None
    reason: str | None = None
    confidence: float | None = None
    at: str | None = None
    round: int = 1
    # Only `started` uses this: the workflow key and the matter's facts.
    # Everything needed to rebuild a run must live IN the events, or the
    # event log is not the source of truth.
    payload: dict | None = None


@dataclass(frozen=True)
class RunState:
    workflow_key: str
    active: tuple[str, ...]
    status: str = RUNNING
    rounds: dict[str, int] = field(default_factory=dict)
    # Facts about the matter that conditions are tested against —
    # value, whether the scope is GxP, whether personal data is involved.
    context: dict = field(default_factory=dict)
    # step id -> the person it is currently with
    assignees: dict[str, str] = field(default_factory=dict)
    events: tuple[Event, ...] = ()

    @property
    def is_open(self) -> bool:
        return self.status in (RUNNING, PAUSED)


def _log(state: RunState, **kw) -> Event:
    return Event(seq=len(state.events) + 1, **kw)


def _enter(
    state: RunState, wf: Workflow, step_id: str, *, at: str | None, assigner=None
) -> RunState:
    """Open a step, count the visit, and put it on someone's desk.

    ``assigner(step, state) -> person | None`` is injected (see
    app/ideal/roster.py). Returning None leaves the step unassigned, which is
    honest and visible — better than handing it to someone already at capacity.
    """
    step = wf.step(step_id)

    # Not applicable to this matter — record why and move on. Recorded, not
    # silent: "Quality did not review" and "Quality was never needed" are
    # different facts and an inspector will want the second one stated.
    if not matches(step.when, state.context):
        ev = Event(
            seq=len(state.events) + 1, kind="not_applicable", step_id=step_id, at=at,
            reason=f"condition not met: {step.when}",
        )
        state = replace(state, events=state.events + (ev,))
        target = step.outcomes.get(APPROVE, END)
        if target == END:
            return _finish(state, step_id, at=at) if not state.active else state
        return _enter(state, wf, target, at=at, assigner=assigner)

    rounds = dict(state.rounds)
    rounds[step_id] = rounds.get(step_id, 0) + 1
    n = rounds[step_id]

    if n > MAX_ROUNDS:
        # Parking beats spinning: a human decides what to do with a negotiation
        # that will not converge.
        ev = Event(
            seq=len(state.events) + 1, kind="parked", step_id=step_id, round=n, at=at,
            reason=f"entered {n} times — exceeds the {MAX_ROUNDS}-round ceiling",
        )
        return replace(state, status=PARKED, rounds=rounds, events=state.events + (ev,))

    if step.is_parallel:
        # Open only the children this matter actually needs — and RECORD the
        # ones it does not. "Quality did not review this" and "Quality was never
        # required" are different facts; an inspector wants the second stated,
        # not inferred from an absence.
        opened, excluded = [], []
        for c in step.parallel:
            (opened if matches(wf.step(c).when, state.context) else excluded).append(c)
        opened = tuple(opened)
        for c in excluded:
            state = replace(state, events=state.events + (Event(
                seq=len(state.events) + 1, kind="not_applicable", step_id=c, at=at,
                reason=f"condition not met: {wf.step(c).when}",
            ),))
        if not opened:
            # Nothing applies — do not hang waiting on an empty group.
            ev = Event(
                seq=len(state.events) + 1, kind="not_applicable", step_id=step_id, at=at,
                reason="no reviewer in this group applies to this matter",
            )
            state = replace(state, rounds=rounds, events=state.events + (ev,))
            target = step.outcomes.get(APPROVE, END)
            if target == END:
                return _finish(state, step_id, at=at)
            return _enter(state, wf, target, at=at, assigner=assigner)
        ev = Event(seq=len(state.events) + 1, kind="entered", step_id=step_id, round=n, at=at)
        state = replace(state, rounds=rounds, events=state.events + (ev,))
        for child in opened:
            state = _enter(state, wf, child, at=at, assigner=assigner)
        return replace(state, active=tuple(dict.fromkeys(state.active + opened)))

    ev = Event(seq=len(state.events) + 1, kind="entered", step_id=step_id, round=n, at=at)
    state = replace(
        state,
        active=tuple(dict.fromkeys(state.active + (step_id,))),
        rounds=rounds,
        events=state.events + (ev,),
        status=RUNNING if state.status == PAUSED else state.status,
    )
    # Assign whenever the step declares an owner — `kind` says who DOES the
    # work, `owner` says who is accountable for it. A system step still needs
    # a name against it when the automation fails. Parallel containers are
    # structural, not real work, so they are skipped.
    if assigner is not None and step.owner and not step.is_parallel:
        person = assigner(step, state)
        if person:
            state = assign(state, wf, step_id=step_id, to=person, by="auto", at=at)
    return state


def start(
    wf: Workflow, *, at: str | None = None, assigner=None, context: dict | None = None
) -> RunState:
    """Begin a run. Nothing happens until someone starts it — deliberately.

    ``context`` carries the facts conditions are tested against: the value,
    whether the scope is GxP, whether personal data is involved.
    """
    ctx = dict(context or {})
    seed = RunState(
        workflow_key=wf.key, active=(), context=ctx,
        events=(Event(seq=1, kind='started', step_id=wf.start, at=at,
                      payload={'workflow_key': wf.key, 'context': ctx}),),
    )
    return _enter(seed, wf, wf.start, at=at, assigner=assigner)


def _finish(state: RunState, step_id: str, *, at: str | None) -> RunState:
    """End the run, on the record. Status is derived from events on replay, so
    a silent status change would be invisible to a rebuilt run."""
    ev = Event(seq=len(state.events) + 1, kind="finished", step_id=step_id, at=at)
    return replace(state, status=DONE, active=(), events=state.events + (ev,))


def _parent_of(wf: Workflow, step_id: str) -> str | None:
    for s in wf.steps.values():
        if step_id in s.parallel:
            return s.id
    return None


# How much an outcome "weighs" when reviewers in a parallel group disagree.
# The most severe answer wins: if Quality wants changes and Finance approves,
# the work goes back. Letting the last answer win would silently discard a
# reviewer's objection — which is how a control quietly stops working.
_SEVERITY = {
    APPROVE: 0,
    APPROVE_WITH_COMMENTS: 1,
    ESCALATE: 2,
    REQUEST_CHANGES: 3,
    REJECT: 4,
}


def _group_outcome(state: RunState, parent: Step) -> str:
    """The governing outcome for a finished parallel group — the most severe
    answer any of its members gave in this round."""
    opened_at = 0
    for ev in state.events:
        if ev.kind == "entered" and ev.step_id == parent.id:
            opened_at = ev.seq
    answers = [
        ev.outcome
        for ev in state.events
        if ev.seq > opened_at
        and ev.kind == "decided"
        and ev.step_id in parent.parallel
        and ev.outcome in _SEVERITY
    ]
    if not answers:
        return APPROVE
    return max(answers, key=lambda o: _SEVERITY[o])


def advance(
    state: RunState,
    wf: Workflow,
    *,
    step_id: str,
    outcome: str,
    actor: str | None = None,
    comment: str | None = None,
    confidence: float | None = None,
    at: str | None = None,
    assigner=None,
) -> RunState:
    """Answer an open step with a typed outcome, and route accordingly.

    This is the whole engine. ``request_changes`` routing to an earlier step is
    the send-back; nothing else is needed to express rework.
    """
    if not state.is_open:
        raise WorkflowError(f"run is {state.status} — it cannot be advanced")
    if step_id not in state.active:
        raise WorkflowError(f"{step_id!r} is not open (open: {list(state.active)})")

    step = wf.step(step_id)
    if outcome not in OUTCOMES:
        raise WorkflowError(f"unknown outcome {outcome!r}")
    if outcome not in step.outcomes:
        raise WorkflowError(
            f"{step_id!r} does not offer {outcome!r} — it offers {sorted(step.outcomes)}"
        )

    ev = _log(
        state, kind="decided", step_id=step_id, actor=actor, outcome=outcome,
        comment=comment, confidence=confidence, at=at,
        round=state.rounds.get(step_id, 1),
    )
    state = replace(state, events=state.events + (ev,))

    # need_info pauses without closing the step — the question stays on someone's
    # desk instead of the run failing.
    if outcome == NEED_INFO:
        return replace(state, status=PAUSED)

    state = replace(state, active=tuple(a for a in state.active if a != step_id))

    if outcome == REJECT:
        return replace(state, status=REJECTED, active=())

    target = step.outcomes[outcome]

    # A child of a parallel group: the group only moves on when its rule is met.
    parent_id = _parent_of(wf, step_id)
    if parent_id is not None:
        parent = wf.step(parent_id)
        siblings_open = [c for c in parent.parallel if c in state.active]
        done_enough = not siblings_open if parent.complete_when == "all" else True
        if not done_enough:
            return state  # still waiting on the others
        state = replace(state, active=tuple(a for a in state.active if a not in parent.parallel))
        # NOT this reviewer's answer — the most severe answer anyone in the group
        # gave. Otherwise whoever happens to answer last overwrites the others.
        governing = _group_outcome(state, parent)
        target = parent.outcomes.get(governing) or parent.outcomes.get(APPROVE) or END

    if target == END:
        return _finish(state, step_id, at=at)
    if target == PAUSE:
        return replace(state, status=PAUSED)

    return _enter(state, wf, target, at=at, assigner=assigner)


def deviate(
    state: RunState,
    wf: Workflow,
    *,
    action: str,          # skip | add_reviewer
    step_id: str,
    reason: str,
    actor: str,
    at: str | None = None,
) -> RunState:
    """Depart from the template, on the record.

    Rigid tools get bypassed — the negotiation finishes over email and the audit
    trail has a hole exactly where the decisions were made. Allowing a reasoned
    departure is what keeps the record complete, so ``reason`` is mandatory.
    """
    if not state.is_open:
        raise WorkflowError(f"run is {state.status} — it cannot be deviated")
    if not (reason or "").strip():
        raise WorkflowError("a deviation must carry a reason")
    if action not in ("skip", "add_reviewer"):
        raise WorkflowError(f"unknown deviation {action!r}")

    step = wf.step(step_id)
    ev = _log(
        state, kind="deviated", step_id=step_id, actor=actor, reason=reason.strip(),
        outcome=action, at=at, round=state.rounds.get(step_id, 1),
    )
    state = replace(state, events=state.events + (ev,))

    if action == "add_reviewer":
        # An extra pair of eyes on an open step; the flow shape is unchanged.
        return state

    if step_id not in state.active:
        raise WorkflowError(f"cannot skip {step_id!r} — it is not open")
    state = replace(state, active=tuple(a for a in state.active if a != step_id))
    target = step.outcomes.get(APPROVE, END)
    if target == END:
        return _finish(state, step_id, at=at) if not state.active else state
    return _enter(state, wf, target, at=at)


# --- who has it, and what they did to it -----------------------------------

def assign(
    state: RunState, wf: Workflow, *, step_id: str, to: str,
    by: str | None = None, at: str | None = None,
) -> RunState:
    """Put an open step on a named person's desk.

    The step's ``owner`` says which department it belongs to; this says who is
    actually holding it. A reassignment is recorded, not overwritten, so "it sat
    with three different people" is answerable.
    """
    if step_id not in state.active:
        raise WorkflowError(f"cannot assign {step_id!r} — it is not open")
    previous = state.assignees.get(step_id)
    ev = _log(
        state, kind="reassigned" if previous else "assigned", step_id=step_id,
        actor=by, comment=to, reason=(f"was {previous}" if previous else None), at=at,
        round=state.rounds.get(step_id, 1),
    )
    return replace(
        state,
        assignees={**state.assignees, step_id: to},
        events=state.events + (ev,),
    )


def record_change(
    state: RunState, wf: Workflow, *, step_id: str, what: str,
    by: str, at: str | None = None,
) -> RunState:
    """Log work done INSIDE a step — an edit, a comment, an uploaded version.

    A step's outcome says how it ended. This says what happened while it was
    open, which is usually the part someone actually wants to read: who changed
    what, in which round.
    """
    if step_id not in state.active:
        raise WorkflowError(f"cannot record work on {step_id!r} — it is not open")
    if not (what or "").strip():
        raise WorkflowError("a change must say what changed")
    ev = _log(
        state, kind="changed", step_id=step_id, actor=by, comment=what.strip(),
        at=at, round=state.rounds.get(step_id, 1),
    )
    return replace(state, events=state.events + (ev,))


def waiting_on(state: RunState, wf: Workflow) -> list[dict]:
    """Who the work is with right now — the "whose turn is it" question."""
    out = []
    for step_id in state.active:
        step = wf.step(step_id)
        if step.is_parallel:
            continue
        out.append({
            "step": step.name,
            "asks": step.asks,
            "owner": step.owner,
            "with": state.assignees.get(step_id),
            "kind": step.kind,
            "sla_hours": step.sla_hours,
            "round": state.rounds.get(step_id, 1),
        })
    return out


# --- reading a run ---------------------------------------------------------

def evidence(state: RunState, wf: Workflow) -> list[dict]:
    """The flat record: every assignment, change, decision and deviation."""
    out: list[dict] = []
    for ev in state.events:
        if ev.kind == "entered":
            continue
        step = wf.steps.get(ev.step_id)
        out.append({
            "seq": ev.seq,
            "step": step.name if step else ev.step_id,
            "asks": step.asks if step else None,
            "kind": ev.kind,
            "outcome": ev.outcome,
            "by": ev.actor,
            "comment": ev.comment,
            "reason": ev.reason,
            "confidence": ev.confidence,
            "round": ev.round,
            "at": ev.at,
        })
    return out


def step_report(state: RunState, wf: Workflow) -> list[dict]:
    """One entry per visit to a step: who held it, what they changed, how it
    ended, and with what confidence.

    This is the "show me each step's output and who approved it" view — grouped
    by step and round rather than a flat log, because that is how a person reads
    it.
    """
    visits: list[dict] = []
    current: dict[tuple[str, int], dict] = {}

    for ev in state.events:
        step = wf.steps.get(ev.step_id)
        key = (ev.step_id, ev.round)
        if key not in current:
            current[key] = {
                "step": step.name if step else ev.step_id,
                "asks": step.asks if step else None,
                "owner": step.owner if step else None,
                "kind": step.kind if step else None,
                "round": ev.round,
                "with": None,
                "changes": [],
                "outcome": None,
                "decided_by": None,
                "comment": None,
                "confidence": None,
                "deviation": None,
                "at": ev.at,
            }
            visits.append(current[key])
        v = current[key]

        if ev.kind in ("assigned", "reassigned"):
            v["with"] = ev.comment
        elif ev.kind == "changed":
            v["changes"].append({"what": ev.comment, "by": ev.actor, "at": ev.at})
        elif ev.kind == "decided":
            v["outcome"] = ev.outcome
            v["decided_by"] = ev.actor
            v["comment"] = ev.comment
            v["confidence"] = ev.confidence
        elif ev.kind == "deviated":
            v["deviation"] = {"action": ev.outcome, "reason": ev.reason, "by": ev.actor}
        elif ev.kind == "parked":
            v["outcome"] = "parked"
            v["comment"] = ev.reason

    return [v for v in visits if v["outcome"] or v["changes"] or v["deviation"] or v["with"]]


def rounds_of(state: RunState, step_id: str) -> int:
    """How many times a step has been entered — the negotiation round count."""
    return state.rounds.get(step_id, 0)
