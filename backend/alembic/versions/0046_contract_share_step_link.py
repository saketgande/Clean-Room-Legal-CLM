"""Link a contract share to the workflow step that issued it.

Adds ``contract_share.workflow_step_run_id`` (nullable, indexed) and
``contract_share.submitted_at`` for the workflow "Send to counterparty" step:
the public Submit call resolves the step from the share, and stamps
``submitted_at`` when the counterparty finishes (which also expires the link).
Purely additive.

Revision ID: 0046_contract_share_step_link
Revises: 0045_backfill_role_permissions
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0046_contract_share_step_link"
down_revision = "0045_backfill_role_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contract_share", sa.Column("workflow_step_run_id", sa.String(36), nullable=True))
    op.add_column("contract_share", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_contract_share_workflow_step_run_id", "contract_share", ["workflow_step_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_contract_share_workflow_step_run_id", table_name="contract_share")
    op.drop_column("contract_share", "submitted_at")
    op.drop_column("contract_share", "workflow_step_run_id")
