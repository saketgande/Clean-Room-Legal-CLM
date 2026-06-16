"""approver groups and multi-step approval routing

Revision ID: 0011_approval_groups_and_steps
Revises: 0010_critical_integrity_fixes
Create Date: 2026-06-15
"""

from alembic import op

revision = "0011_approval_groups_and_steps"
down_revision = "0010_critical_integrity_fixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Approver groups (named pools of approvers) ------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approver_group (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            CONSTRAINT uq_approver_group_org_name UNIQUE (org_id, name)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_approver_group_org_id ON approver_group (org_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approver_group_member (
            group_id VARCHAR(36) NOT NULL REFERENCES approver_group(id) ON DELETE CASCADE,
            user_id VARCHAR(36) NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approver_group_member_user_id "
        "ON approver_group_member (user_id)"
    )

    # --- Ordered steps of a routing rule ----------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_routing_step (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            rule_id VARCHAR(36) NOT NULL REFERENCES approval_routing_rule(id) ON DELETE CASCADE,
            step_order INTEGER NOT NULL DEFAULT 1,
            approver_group_id VARCHAR(36) REFERENCES approver_group(id),
            approver_user_id VARCHAR(36) REFERENCES "user"(id),
            approver_role VARCHAR(120),
            mode VARCHAR(20) NOT NULL DEFAULT 'any',
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_routing_step_org_id "
        "ON approval_routing_step (org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_routing_step_rule_id "
        "ON approval_routing_step (rule_id)"
    )

    # --- Stage tracking on approval_request -------------------------------
    op.execute(
        "ALTER TABLE approval_request "
        "ADD COLUMN IF NOT EXISTS approver_group_id VARCHAR(36) REFERENCES approver_group(id)"
    )
    op.execute(
        "ALTER TABLE approval_request "
        "ADD COLUMN IF NOT EXISTS routing_rule_id VARCHAR(36) REFERENCES approval_routing_rule(id)"
    )
    op.execute(
        "ALTER TABLE approval_request "
        "ADD COLUMN IF NOT EXISTS step_order INTEGER NOT NULL DEFAULT 1"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_request_approver_group_id "
        "ON approval_request (approver_group_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_approval_request_approver_group_id")
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS step_order")
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS routing_rule_id")
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS approver_group_id")
    op.execute("DROP TABLE IF EXISTS approval_routing_step")
    op.execute("DROP TABLE IF EXISTS approver_group_member")
    op.execute("DROP TABLE IF EXISTS approver_group")
