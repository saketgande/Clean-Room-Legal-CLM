"""Post-filing tracking: the next date something is due with the office.

Additive only. The new post-filing statuses (examination, published, opposition_period, refused,
withdrawn) need no schema change: ``trademark.status`` is a plain string.

Revision ID: 0076_post_filing_tracking
Revises: 0075_merge_agent_filing
"""

import sqlalchemy as sa

from alembic import op

revision = "0076_post_filing_tracking"
down_revision = "0075_merge_agent_filing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trademark", sa.Column("next_action_on", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trademark", sa.Column("next_action_note", sa.Text(), nullable=True))
    op.create_index("ix_trademark_next_action_on", "trademark", ["next_action_on"])


def downgrade() -> None:
    op.drop_index("ix_trademark_next_action_on", table_name="trademark")
    op.drop_column("trademark", "next_action_note")
    op.drop_column("trademark", "next_action_on")
