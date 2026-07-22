"""approval quorum: a mode column on approval_request so an "all" step waits for
every group member (not just the first) before it advances.

Revision ID: 0027_approval_quorum
Revises: 0026_flow_engine
"""

from alembic import op

revision = "0027_approval_quorum"
down_revision = "0026_flow_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE approval_request "
        "ADD COLUMN IF NOT EXISTS mode VARCHAR(20) NOT NULL DEFAULT 'any';"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS mode;")
