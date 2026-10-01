"""Save as Draft for the agreement wizard.

The button used to discard the form. Drafts get their own table rather than a
"draft" status on intake_request, because a filed request starts triage, the
SLA clock and approvals — none of which may run for an unsubmitted form.
"""

import sqlalchemy as sa

from alembic import op

revision = "0049_intake_drafts"
down_revision = "0048_intake_agreement_form_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intake_draft",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("form_key", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("parent_contract_id", sa.String(36), nullable=True),
        sa.Column("page_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visited", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("intake_draft")
