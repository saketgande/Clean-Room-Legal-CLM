"""Drop intake_request.nda_check: the NDA check was removed (the counterparty card already lists their contracts)."""
import sqlalchemy as sa

from alembic import op

revision = "0079_drop_nda_check"
down_revision = "0078_activate_step_wording"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("intake_request", "nda_check")


def downgrade() -> None:
    op.add_column("intake_request", sa.Column("nda_check", sa.JSON(), nullable=True))
