"""Per-request-type SLA, in business hours.

Every request was created with a hardcoded 24-hour SLA regardless of type or
priority, so a Critical escalation and a routine NDA shared one deadline. NULL
keeps the previous behaviour by falling back to settings.intake_default_sla_hours.

Revision ID: 0044_intake_type_sla_hours
Revises: 0043_db_invariants
"""

import sqlalchemy as sa

from alembic import op

revision = "0044_intake_type_sla_hours"
down_revision = "0043_db_invariants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("intake_request_type", sa.Column("sla_hours", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("intake_request_type", "sla_hours")
