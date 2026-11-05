"""Add intake_request.nda_check (filing record of the NDA-in-force check) and merge heads."""
import sqlalchemy as sa

from alembic import op

revision = "0075_nda_check"
down_revision = ("0074_merge_clearance", "0074_template_sync")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("intake_request", sa.Column("nda_check", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("intake_request", "nda_check")
