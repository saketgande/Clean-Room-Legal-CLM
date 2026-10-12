"""Record how a notice's response deadline was worked out.

`notice.deadline_basis` holds the deadline wording the AI copied from the notice
(period, what it runs from, the quote), the formula code computed the date with,
whether a person or the document set it, and who confirmed it. The date itself
stays in `response_due_date`, so posture, reminders and drafting are unchanged.

Additive and nullable: existing notices have no basis (read as "no deadline
recorded"), so no backfill. Downgrade drops the column.

Revision ID: 0072_notice_deadline_basis
Revises: 0071_ai_call_log_eval_run
"""

import sqlalchemy as sa

from alembic import op

revision = "0072_notice_deadline_basis"
down_revision = "0071_ai_call_log_eval_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notice", sa.Column("deadline_basis", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("notice", "deadline_basis")
