"""Add intake_request.filed_summary and intake_draft.summary_box (spec 013)."""
import sqlalchemy as sa

from alembic import op

revision = "0076_request_summary"
down_revision = "0075_nda_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("intake_request", sa.Column("filed_summary", sa.JSON(), nullable=True))
    op.add_column("intake_draft", sa.Column("summary_box", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("intake_draft", "summary_box")
    op.drop_column("intake_request", "filed_summary")
