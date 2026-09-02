"""Drafted responses and escalation for the notice register.

`escalated_intake_request_id` is the deliberate choice here. Escalating a notice
does NOT get its own bespoke approval/routing machinery — it opens a linked
intake request, which already carries routing rules, team assignment, the SLA
clock and the approval ladder (approval_request.intake_request_id has hung off
intake since the ladder generalised beyond contracts). The notice stays the
system of record for the correspondence; the intake ticket becomes the unit of
work the legal team actually triages.
"""
from alembic import op

revision = "0035_notice_response_escalation"
down_revision = "0034_notice_reminders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notice ADD COLUMN IF NOT EXISTS draft_response TEXT;")
    op.execute("ALTER TABLE notice ADD COLUMN IF NOT EXISTS draft_response_at TIMESTAMPTZ;")
    # ON DELETE SET NULL, not CASCADE: deleting the escalation ticket must never
    # take the notice (and its statutory deadline) down with it.
    op.execute(
        """
        ALTER TABLE notice
        ADD COLUMN IF NOT EXISTS escalated_intake_request_id VARCHAR(36)
        REFERENCES intake_request(id) ON DELETE SET NULL;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notice_escalated_intake_request_id "
        "ON notice (escalated_intake_request_id);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notice DROP COLUMN IF EXISTS escalated_intake_request_id;")
    op.execute("ALTER TABLE notice DROP COLUMN IF EXISTS draft_response_at;")
    op.execute("ALTER TABLE notice DROP COLUMN IF EXISTS draft_response;")
