"""Rename the workflow-engine + prompt-library tables to match the code rename.

The engine (formerly `flows`) becomes `workflows`, and the Prompt Library
(formerly squatting on `workflows`) vacates that name for `prompt*`. Class
names drive `__tablename__` (snake_case), so the physical tables must follow:

  Prompt Library (empty tables):  workflow -> prompt, workflow_version ->
    prompt_version, workflow_run -> prompt_run
  Engine (live data):             flow -> workflow, flow_run -> workflow_run,
    flow_step_run -> workflow_step_run

Order matters: the Prompt Library must free the `workflow`/`workflow_run` names
BEFORE the engine renames into them. `rename_table` preserves the columns,
inbound foreign keys and data — only the table name changes.
"""
from alembic import op

revision = "0036_workflow_rename"
down_revision = "0035_notice_response_escalation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Prompt Library vacates the "workflow" names first (these tables are empty).
    op.rename_table("workflow", "prompt")
    op.rename_table("workflow_version", "prompt_version")
    op.rename_table("workflow_run", "prompt_run")
    # 2. The engine moves into the freed names.
    op.rename_table("flow", "workflow")
    op.rename_table("flow_run", "workflow_run")
    op.rename_table("flow_step_run", "workflow_step_run")


def downgrade() -> None:
    op.rename_table("workflow_step_run", "flow_step_run")
    op.rename_table("workflow_run", "flow_run")
    op.rename_table("workflow", "flow")
    op.rename_table("prompt_run", "workflow_run")
    op.rename_table("prompt_version", "workflow_version")
    op.rename_table("prompt", "workflow")
