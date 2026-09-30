"""Counterparty revision rounds: their returned version compared with ours.

One round per returned version (the version we sent against theirs); one
change row per clause that differs, holding the word difference and our
decision on it. See app/contract_files/revisions.py.
"""

import sqlalchemy as sa

from alembic import op

revision = "0063_revision_rounds"
down_revision = "0062_remove_matters"
branch_labels = None
depends_on = None


def _base() -> list:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "revision_round",
        *_base(),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("contract.id"), nullable=False, index=True),
        sa.Column("base_version_id", sa.String(36), sa.ForeignKey("contract_version.id"), nullable=False),
        sa.Column("revision_version_id", sa.String(36), sa.ForeignKey("contract_version.id"), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open", index=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("outcome_version_id", sa.String(36), sa.ForeignKey("contract_version.id"), nullable=True),
        sa.Column("tracked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "revision_change",
        *_base(),
        sa.Column("round_id", sa.String(36), sa.ForeignKey("revision_round.id"), nullable=False, index=True),
        sa.Column("contract_id", sa.String(36), sa.ForeignKey("contract.id"), nullable=False, index=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(120), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("unmarked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("our_text", sa.Text(), nullable=True),
        sa.Column("their_text", sa.Text(), nullable=True),
        sa.Column("parts", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("decision", sa.String(20), nullable=False, server_default="open"),
        sa.Column("counter_text", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.String(36), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("revision_change")
    op.drop_table("revision_round")
