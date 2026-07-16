"""conditional & parallel approval steps

Adds stage grouping (steps sharing a stage run in parallel — all must clear
before the chain advances) and per-step conditions (a step only joins the chain
when its condition matches the contract). Approval requests carry the stage so
activation can advance a whole parallel stage at once.

Revision ID: 0019_conditional_parallel_steps
Revises: 0018_routing_rule_soft_delete
Create Date: 2026-07-16
"""

from alembic import op

revision = "0019_conditional_parallel_steps"
down_revision = "0018_routing_rule_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Parallel grouping + conditional inclusion on the rule's steps. stage
    # backfills to step_order so every existing step stays its own sequential
    # stage — identical behaviour to before this migration.
    op.execute("ALTER TABLE approval_routing_step ADD COLUMN IF NOT EXISTS stage INTEGER")
    op.execute("UPDATE approval_routing_step SET stage = step_order WHERE stage IS NULL")
    op.execute("ALTER TABLE approval_routing_step ADD COLUMN IF NOT EXISTS condition JSON")

    # The live request carries its stage so a decision can tell when a whole
    # parallel stage is complete. Backfill to step_order for in-flight chains.
    op.execute("ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS stage INTEGER")
    op.execute("UPDATE approval_request SET stage = step_order WHERE stage IS NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS stage")
    op.execute("ALTER TABLE approval_routing_step DROP COLUMN IF EXISTS condition")
    op.execute("ALTER TABLE approval_routing_step DROP COLUMN IF EXISTS stage")
