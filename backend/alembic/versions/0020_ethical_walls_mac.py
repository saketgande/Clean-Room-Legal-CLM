"""ethical walls + confidentiality/clearance (legal RBAC phase 3)

Adds the deny-override layers on top of the additive grant model:
  * ``contract.confidentiality`` + ``user.clearance`` — mandatory access control
    (a user may only read a contract classified at or below their clearance);
  * ``ethical_wall`` (+ ``ethical_wall_principal``) — conflict-of-interest screens
    that hard-deny named principals from a contract or an entire project/matter,
    overriding ownership, grants and admin alike.

Both fold into app/contracts/access.py so the same rule applies to list views,
row checks and the RAG retrieval path.

Revision ID: 0020_ethical_walls_mac
Revises: 0019_resource_grants
Create Date: 2026-07-10
"""

from alembic import op

revision = "0020_ethical_walls_mac"
down_revision = "0019_resource_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- MAC columns (default-safe: nothing gets locked down harder until set) ---
    op.execute(
        "ALTER TABLE contract ADD COLUMN IF NOT EXISTS confidentiality VARCHAR(40) NOT NULL DEFAULT 'internal'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contract_confidentiality ON contract(confidentiality)"
    )
    op.execute(
        "ALTER TABLE \"user\" ADD COLUMN IF NOT EXISTS clearance VARCHAR(40) NOT NULL DEFAULT 'confidential'"
    )

    # --- ethical walls ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ethical_wall (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            name VARCHAR(200) NOT NULL,
            reason TEXT,
            scope_type VARCHAR(40) NOT NULL,
            scope_id VARCHAR(36) NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            deactivated_at TIMESTAMPTZ,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_ethical_wall_org_id ON ethical_wall(org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ethical_wall_active ON ethical_wall(active)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ethical_wall_scope ON ethical_wall(scope_type, scope_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ethical_wall_principal (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            wall_id VARCHAR(36) NOT NULL REFERENCES ethical_wall(id) ON DELETE CASCADE,
            principal_type VARCHAR(40) NOT NULL,
            principal_id VARCHAR(36) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ethical_wall_principal_wall_id ON ethical_wall_principal(wall_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ethical_wall_principal_principal ON ethical_wall_principal(principal_type, principal_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ethical_wall_principal")
    op.execute("DROP TABLE IF EXISTS ethical_wall")
    op.execute("DROP INDEX IF EXISTS ix_contract_confidentiality")
    op.execute("ALTER TABLE contract DROP COLUMN IF EXISTS confidentiality")
    op.execute('ALTER TABLE "user" DROP COLUMN IF EXISTS clearance')
