"""workflow versioning + per-user sharing

Adds an edit-history table for prompts (workflow_version, mirroring
playbook_version) and a shared_user_ids column on workflow so the
shared_with_users visibility can name the specific people a prompt is
shared with. Both changes are additive and safe to re-run.

Revision ID: 0017_workflow_versioning
Revises: 0016_contract_risk_score
Create Date: 2026-07-07
"""

from alembic import op

revision = "0017_workflow_versioning"
down_revision = "0016_contract_risk_score"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfills existing rows to an empty list via the non-volatile default.
    op.execute(
        "ALTER TABLE workflow ADD COLUMN IF NOT EXISTS shared_user_ids JSON DEFAULT '[]'::json"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_version (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            workflow_id VARCHAR(36) NOT NULL REFERENCES workflow(id),
            version_number INTEGER NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            definition JSON NOT NULL DEFAULT '{}'::json,
            visibility VARCHAR(80) NOT NULL DEFAULT 'private',
            note TEXT,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_version_workflow_id ON workflow_version(workflow_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_version_org_id ON workflow_version(org_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workflow_version")
    op.execute("ALTER TABLE workflow DROP COLUMN IF EXISTS shared_user_ids")
