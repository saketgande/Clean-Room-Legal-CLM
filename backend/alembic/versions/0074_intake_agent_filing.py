"""Intake requests filed by external agents: idempotency key/hash + filing API key.

Additive and nullable, no backfill: existing requests read as "not filed by an
agent" and carry no idempotency key.
"""

import sqlalchemy as sa

from alembic import op

revision = "0074_intake_agent_filing"
down_revision = "0073_merge_trademark_suite"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("intake_request", sa.Column("idempotency_key", sa.String(128), nullable=True))
    op.add_column("intake_request", sa.Column("idempotency_hash", sa.String(64), nullable=True))
    op.add_column(
        "intake_request", sa.Column("filed_via_api_key_id", sa.String(36), nullable=True)
    )
    op.create_foreign_key(
        "fk_intake_request_filed_via_api_key_id_api_key",
        "intake_request",
        "api_key",
        ["filed_via_api_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_intake_request_filed_via_api_key_id", "intake_request", ["filed_via_api_key_id"]
    )
    op.create_index(
        "uq_intake_request_org_idemkey",
        "intake_request",
        ["org_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_intake_request_org_idemkey", table_name="intake_request")
    op.drop_index("ix_intake_request_filed_via_api_key_id", table_name="intake_request")
    op.drop_constraint(
        "fk_intake_request_filed_via_api_key_id_api_key", "intake_request", type_="foreignkey"
    )
    op.drop_column("intake_request", "filed_via_api_key_id")
    op.drop_column("intake_request", "idempotency_hash")
    op.drop_column("intake_request", "idempotency_key")
