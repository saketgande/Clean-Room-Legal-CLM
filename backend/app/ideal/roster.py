"""Who is in each department, and whose turn it is.

A step declares the department it belongs to (``Step.owner``). Something has to
turn that into a named person when the step opens, or the work sits in a queue
nobody owns — which is the failure the live application has today: routing picks
a person, forgets the pool, and a step with no assignee is a step nobody does.

Two strategies, both deterministic:

    least_loaded  the person holding the fewest open steps right now
    round_robin   the person who went longest without one

Load is counted from the run itself plus anything the caller knows about other
runs, so the same person is not handed five contracts at once across five
workflows.

Injected, like everything else here: ``auto_assigner()`` returns a plain
function that the engine calls when a step opens. No database, no globals.
"""

from __future__ import annotations

from dataclasses import dataclass

LEAST_LOADED, ROUND_ROBIN = "least_loaded", "round_robin"


@dataclass(frozen=True)
class Member:
    person: str
    department: str
    # 0 = unbounded. A capacity that is reached takes someone out of the running
    # rather than silently overloading them.
    capacity: int = 0
    active: bool = True
    # For round-robin: lower means longer since they were last picked.
    last_assigned_seq: int = 0


@dataclass(frozen=True)
class Roster:
    members: tuple[Member, ...] = ()
    # Where to send work when a department is full or empty — the overflow chain
    # the live app models on teams but never uses.
    overflow: dict[str, str] | None = None

    def of(self, department: str) -> list[Member]:
        return [m for m in self.members if m.department == department and m.active]

    def departments(self) -> list[str]:
        return sorted({m.department for m in self.members})


def pick(
    roster: Roster,
    department: str,
    *,
    load: dict[str, int] | None = None,
    strategy: str = LEAST_LOADED,
    _seen: frozenset[str] = frozenset(),
) -> str | None:
    """Choose a person from ``department``, or None if there is nobody free.

    Returning None is deliberate: an unassigned step is honest and visible,
    whereas assigning to someone over capacity hides the problem.
    """
    load = load or {}
    candidates = [
        m for m in roster.of(department)
        if m.capacity == 0 or load.get(m.person, 0) < m.capacity
    ]

    if not candidates:
        # Everyone is full (or the department is empty) — try the overflow.
        nxt = (roster.overflow or {}).get(department)
        if nxt and nxt not in _seen:
            return pick(roster, nxt, load=load, strategy=strategy, _seen=_seen | {department})
        return None

    if strategy == ROUND_ROBIN:
        # Longest since last picked; person name breaks ties so it is stable.
        return min(candidates, key=lambda m: (m.last_assigned_seq, m.person)).person

    # least_loaded — fewest open items, name breaks ties.
    return min(candidates, key=lambda m: (load.get(m.person, 0), m.person)).person


def load_from_run(state) -> dict[str, int]:
    """How many OPEN steps each person is currently holding in this run."""
    counts: dict[str, int] = {}
    for step_id, person in state.assignees.items():
        if step_id in state.active:
            counts[person] = counts.get(person, 0) + 1
    return counts


def auto_assigner(
    roster: Roster,
    *,
    strategy: str = LEAST_LOADED,
    other_load: dict[str, int] | None = None,
):
    """Build the function the engine calls when a step opens.

    ``other_load`` is what this person is carrying on OTHER matters. Without it
    the balancer only sees one contract and cheerfully gives the same lawyer
    every step of every workflow.
    """

    def assigner(step, state) -> str | None:
        if not step.owner:
            return None
        load = dict(other_load or {})
        for person, n in load_from_run(state).items():
            load[person] = load.get(person, 0) + n
        return pick(roster, step.owner, load=load, strategy=strategy)

    return assigner
