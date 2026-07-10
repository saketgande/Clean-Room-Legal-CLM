"""unified resource grants (legal RBAC phase 2)

One table for object-level, time-bound access grants — object#level@principal
tuples that generalize ProjectShare / ContractShare / Workflow.shared_user_ids
into a single relationship model. Additive to the existing access paths.

Revision ID: 0019_resource_grants
Revises: 0018_drop_rls_single_tenant
Create Date: 2026-07-08
"""

from alembic import op

revision = "0019_resource_grants"
down_revision = "0018_drop_rls_single_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_grant (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            principal_type VARCHAR(40) NOT NULL,
            principal_id VARCHAR(36) NOT NULL,
            resource_type VARCHAR(60) NOT NULL,
            resource_id VARCHAR(36) NOT NULL,
            access_level VARCHAR(40) NOT NULL DEFAULT 'read',
            note TEXT,
            valid_from TIMESTAMPTZ,
            valid_until TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_resource_grant_org_id ON resource_grant(org_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_resource_grant_resource ON resource_grant(resource_type, resource_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_resource_grant_principal ON resource_grant(principal_type, principal_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS resource_grant")
