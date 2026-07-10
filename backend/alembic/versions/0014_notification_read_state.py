"""notification read state (read_at) for in-app unread badge / mark-as-read

Revision ID: 0014_notification_read_state
Revises: 0013_contract_comments
Create Date: 2026-07-07
"""

from alembic import op

revision = "0014_notification_read_state"
down_revision = "0013_contract_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE notification ADD COLUMN IF NOT EXISTS read_at TIMESTAMP WITH TIME ZONE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_read_at ON notification (read_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notification_read_at")
    op.execute("ALTER TABLE notification DROP COLUMN IF EXISTS read_at")
