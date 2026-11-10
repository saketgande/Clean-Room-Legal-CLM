"""Stop the last workflow step claiming that obligations are tracked.

Every starter workflow ended with an ``ai_task`` step named "Activate & track" whose instructions said "File the
contract and start obligation tracking." The step checks and does nothing: signing queues the reading of the
contract's obligations, and the Active stage's own row says how that went. The step row read "Done" in green above a
red "Obligation extraction failed". Its name is now "Activate" and its text no longer claims anything
(``app.workflows.stages.ACTIVATE_STEP_*``); this migration gives the workflows that already exist the same wording.

Three places hold the wording: ``workflow.steps`` (the definition), ``workflow_run.steps`` (the pinned snapshot a
running or finished request keeps) and ``workflow_step_run.step_name`` (what each step run shows). Only an ``ai_task``
step named exactly "Activate & track" is renamed; its instructions are replaced only when they are still the old
sentence, so an administrator's own text stays. Nothing depends on the name (the engine keys off the step's type and
stage). A data migration writes no audit row: mention it in the release note.

Downgrade puts the old name back on every ``ai_task`` named "Activate" and the old sentence on the ones that still
carry the new one. Best effort: a step an administrator named "Activate" after this ran is renamed too.

Revision ID: 0078_activate_step_wording
Revises: 0077_std_paper
Create Date: 2026-10-28
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import context, op

revision = "0078_activate_step_wording"
down_revision = "0077_std_paper"
branch_labels = None
depends_on = None

OLD_NAME = "Activate & track"
OLD_TEXT = "File the contract and start obligation tracking."
NEW_NAME = "Activate"
NEW_TEXT = (
    "Closes out the workflow. Obligations are read on their own once the contract is Active, and the Active stage "
    "shows how that went.")


def rewrite_steps(steps, *, old_name, new_name, old_text, new_text):
    """(steps, changed): `steps` with every ai_task named `old_name` renamed, and its instructions replaced when they equal
    `old_text`. Pure; a list that is not a list of dicts comes back untouched."""
    if not isinstance(steps, list):
        return steps, False
    changed = False
    out = []
    for step in steps:
        if isinstance(step, dict) and step.get("type") == "ai_task" and step.get("name") == old_name:
            step = {**step, "name": new_name}
            config = step.get("config")
            if isinstance(config, dict) and config.get("instructions") == old_text:
                step["config"] = {**config, "instructions": new_text}
            changed = True
        out.append(step)
    return out, changed


def reword(bind, *, reverse: bool = False) -> dict:
    """Rewrite the wording in the workflow definitions, the pinned run snapshots and the step runs. Returns how many
    rows of each it changed. Idempotent. Takes the connection so a test can run it inside its own transaction."""
    old_name, new_name, old_text, new_text = (
        (NEW_NAME, OLD_NAME, NEW_TEXT, OLD_TEXT) if reverse else (OLD_NAME, NEW_NAME, OLD_TEXT, NEW_TEXT))
    counts = {"workflow": 0, "workflow_run": 0, "workflow_step_run": 0}
    for table in ("workflow", "workflow_run"):
        rows = bind.execute(
            sa.text(f"SELECT id, steps FROM {table} WHERE steps::text LIKE :needle"), {"needle": f"%{old_name}%"}
        ).all()
        for row_id, steps in rows:
            new_steps, changed = rewrite_steps(steps, old_name=old_name, new_name=new_name, old_text=old_text,
                                               new_text=new_text)
            if changed:
                bind.execute(sa.text(f"UPDATE {table} SET steps = CAST(:steps AS json) WHERE id = :id"),
                             {"steps": json.dumps(new_steps), "id": row_id})
                counts[table] += 1
    counts["workflow_step_run"] = bind.execute(
        sa.text("UPDATE workflow_step_run SET step_name = :new WHERE step_type = 'ai_task' AND step_name = :old"),
        {"new": new_name, "old": old_name},
    ).rowcount or 0
    return counts


def upgrade() -> None:
    # Pure data migration: nothing to emit for `alembic upgrade head --sql` (same guard as 0077_obligation_edit_roles).
    if not context.is_offline_mode():
        reword(op.get_bind())


def downgrade() -> None:
    if not context.is_offline_mode():
        reword(op.get_bind(), reverse=True)
