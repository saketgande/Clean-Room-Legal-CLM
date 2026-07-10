"""delegation of authority / ABAC action gate (legal RBAC phase 4)

Adds ``authority_grant`` — per-principal, per-action authority policies keyed off
contract attributes (value, type, jurisdiction, risk band). Enforced at the
approval-decision and signature-initiation seams. Progressive: an action is only
gated once at least one policy for it exists.

Revision ID: 0021_authority_grants
Revises: 0020_ethical_walls_mac
Create Date: 2026-07-10
"""

from alembic import op

revision = "0021_authority_grants"
down_revision = "0020_ethical_walls_mac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS authority_grant (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            principal_type VARCHAR(40) NOT NULL,
            principal_id VARCHAR(36) NOT NULL,
            action VARCHAR(60) NOT NULL,
            max_value DOUBLE PRECISION,
            currency VARCHAR(3),
            allowed_contract_types JSONB,
            allowed_jurisdictions JSONB,
            max_risk_band VARCHAR(40),
            delegated_by_user_id VARCHAR(36) REFERENCES "user"(id),
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
    op.execute("CREATE INDEX IF NOT EXISTS ix_authority_grant_org_id ON authority_grant(org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_authority_grant_action ON authority_grant(action)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_authority_grant_principal ON authority_grant(principal_type, principal_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS authority_grant")
