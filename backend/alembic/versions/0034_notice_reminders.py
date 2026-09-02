"""Deadline reminders for the notice register.

Deliberately a single column rather than a notice_reminder table. Obligations
need their own reminder rows because their remind-dates are arbitrary and
user-set; a notice's reminder schedule is *derived* from its statutory deadline
(7 days out, 3 days out, due today, lapsed), so the only state worth persisting
is the most urgent milestone we've already told the owner about. Comparing the
current milestone against that is what stops the daily sweep re-sending.
"""
from alembic import op

revision = "0034_notice_reminders"
down_revision = "0033_notice_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE notice ADD COLUMN IF NOT EXISTS last_reminder_stage VARCHAR(10);"
    )
    op.execute(
        "ALTER TABLE notice ADD COLUMN IF NOT EXISTS last_reminder_at TIMESTAMPTZ;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notice DROP COLUMN IF EXISTS last_reminder_at;")
    op.execute("ALTER TABLE notice DROP COLUMN IF EXISTS last_reminder_stage;")
