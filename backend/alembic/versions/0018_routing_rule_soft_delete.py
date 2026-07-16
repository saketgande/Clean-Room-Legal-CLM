"""routing rule soft-delete

Routing rules were create-only and permanent. Adds a soft-delete marker so a
rule can be retired without breaking the historical ApprovalRequest.routing_rule_id
references that point at it — the resolver simply skips deleted rules.

Revision ID: 0018_routing_rule_soft_delete
Revises: 0017_workflow_versioning
Create Date: 2026-07-16
"""

from alembic import op

revision = "0018_routing_rule_soft_delete"
down_revision = "0017_workflow_versioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE approval_routing_rule "
        "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE approval_routing_rule "
        "ADD COLUMN IF NOT EXISTS deleted_by_user_id VARCHAR(36)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE approval_routing_rule DROP COLUMN IF EXISTS deleted_by_user_id")
    op.execute("ALTER TABLE approval_routing_rule DROP COLUMN IF EXISTS deleted_at")
