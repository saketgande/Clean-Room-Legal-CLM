"""Give every workflow step a lifecycle stage (``step["stage"]``).

Contracts all follow Intake → Drafting → Review → Approval → Signature →
Active → Closed; a workflow's steps now sit under those stages. This fills in
the stage on saved workflows and on runs' step snapshots, by the rules in
app/workflows/stages.py (copied here so this migration never changes with them).

Saved workflows are also put in lifecycle order: a counterparty negotiation
that came after an approval (the built-in MSA had one) moves to just before
the first approval — negotiation is Review, and approval signs off the agreed
terms. Runs are only labelled, never reordered: their step positions are live.
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "0057_workflow_step_stages"
down_revision = "0056_merge_into_teams"
branch_labels = None
depends_on = None

_STAGES = ("intake", "drafting", "review", "approval", "signature", "active", "closed")
_RANK = {s: i for i, s in enumerate(_STAGES)}
_FIXED = {"clm_draft": "drafting", "approval": "approval", "signature": "signature"}
_ALLOWED = {"clm_draft": ("drafting",), "approval": ("approval",), "signature": ("signature",),
            "counterparty": ("drafting", "review")}
# The built-in library's logging / docketing steps belong to Intake.
_INTAKE_NAMES = {"incident logging", "matter intake & docketing", "notice logging",
                 "action logging & classification", "complaint triage", "matter intake",
                 "request & draft resolution"}


def _allowed(t):
    return _ALLOWED.get(t, _STAGES)


def _infer(steps, builtin):
    nxt, fixed_after = None, [None] * len(steps)
    for i in range(len(steps) - 1, -1, -1):
        fixed_after[i] = nxt
        if steps[i].get("type") in _FIXED:
            nxt = _RANK[_FIXED[steps[i]["type"]]]
    floor = 0
    for i, st in enumerate(steps):
        t, stage = st.get("type"), st.get("stage")
        if stage not in _allowed(t):
            if t in _FIXED:
                stage = _FIXED[t]
            elif builtin and (st.get("name") or "").strip().lower() in _INTAKE_NAMES and floor == 0:
                stage = "intake"
            elif st.get("parallel") and i > 0:
                stage = steps[i - 1]["stage"]
            else:
                cap = fixed_after[i]
                want = _RANK["intake"] if cap is not None and cap < _RANK["review"] else _RANK["review"]
                stage = _STAGES[max(floor, want)]
                if stage not in _allowed(t):
                    stage = _allowed(t)[-1]
            st["stage"] = stage
        floor = max(floor, _RANK[stage])
        if t == "signature":
            floor = max(floor, _RANK["active"])
    return steps


def _negotiation_before_approval(steps):
    first = next((i for i, s in enumerate(steps) if s.get("type") == "approval"), None)
    if first is None:
        return steps
    late = [s for i, s in enumerate(steps) if i > first and s.get("type") == "counterparty"]
    if not late:
        return steps
    rest = [s for s in steps if s not in late]
    at = next(i for i, s in enumerate(rest) if s.get("type") == "approval")
    for s in late:
        s.pop("parallel", None)
    return rest[:at] + late + rest[at:]


def upgrade() -> None:
    if op.get_context().as_sql:
        return  # offline SQL: data backfill needs the rows
    bind = op.get_bind()
    for table in ("workflow", "workflow_run"):
        builtin_col = ", is_builtin" if table == "workflow" else ", false"
        for row_id, steps, builtin in bind.execute(sa.text(f"SELECT id, steps{builtin_col} FROM {table}")).all():
            steps = json.loads(steps) if isinstance(steps, str) else (steps or [])
            if table == "workflow":
                steps = _negotiation_before_approval(steps)
            steps = _infer(steps, bool(builtin))
            bind.execute(sa.text(f"UPDATE {table} SET steps = CAST(:s AS JSON) WHERE id = :id"),
                         {"s": json.dumps(steps), "id": row_id})


def downgrade() -> None:
    pass  # a stage label is harmless to older code, which ignores it
