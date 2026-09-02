"""Team serves multiple departments (business units).

A legal team typically supports several business units (e.g. Commercial Legal
serves Sales + Product + Procurement), so the single `business_unit` string from
0037 becomes a `departments` list. Uses the same vocabulary as the intake form's
department dropdown, so a request's `department` matches a team's served list.
The 0037 column was never populated, so dropping it loses nothing.
"""
import sqlalchemy as sa
from alembic import op

revision = "0038_team_departments"
down_revision = "0037_team_expertise"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("intake_team", "business_unit")
    op.add_column("intake_team", sa.Column("departments", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("intake_team", "departments")
    op.add_column("intake_team", sa.Column("business_unit", sa.String(120), nullable=True))
