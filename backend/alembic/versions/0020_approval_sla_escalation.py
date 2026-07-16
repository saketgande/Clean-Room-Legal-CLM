"""approval SLA escalation

Per-step SLA window + escalation target on routing steps, and escalation
bookkeeping on the live request. A sweep re-notifies the backup approver when a
step blows past its due date.

Revision ID: 0020_approval_sla_escalation
Revises: 0019_conditional_parallel_steps
Create Date: 2026-07-16
"""

from alembic import op

revision = "0020_approval_sla_escalation"
down_revision = "0019_conditional_parallel_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE approval_routing_step ADD COLUMN IF NOT EXISTS sla_hours INTEGER")
    op.execute(
        "ALTER TABLE approval_routing_step ADD COLUMN IF NOT EXISTS escalation_group_id VARCHAR(36)"
    )
    op.execute(
        "ALTER TABLE approval_routing_step ADD COLUMN IF NOT EXISTS escalation_user_id VARCHAR(36)"
    )
    # The live request carries its escalation target (denormalized at submit) so
    # the overdue sweep never has to re-resolve the rule.
    op.execute("ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS escalation_group_id VARCHAR(36)"
    )
    op.execute(
        "ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS escalation_user_id VARCHAR(36)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS escalation_user_id")
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS escalation_group_id")
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS escalated_at")
    op.execute("ALTER TABLE approval_routing_step DROP COLUMN IF EXISTS escalation_user_id")
    op.execute("ALTER TABLE approval_routing_step DROP COLUMN IF EXISTS escalation_group_id")
    op.execute("ALTER TABLE approval_routing_step DROP COLUMN IF EXISTS sla_hours")
