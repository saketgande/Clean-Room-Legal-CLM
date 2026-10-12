"""Legal-entity and counterparty master records, linked from intake requests.

Both were free text typed into each request (the entity list was three
hard-coded names in the frontend), so nothing tied a request to a real record.
"""

import sqlalchemy as sa

from alembic import op

revision = "0050_parties_master_records"
down_revision = "0049_intake_drafts"
branch_labels = None
depends_on = None


def _common() -> list:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("jurisdiction", sa.String(120), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(40), nullable=False, server_default="aegis"),
        sa.Column("external_ref", sa.String(120), nullable=True),
        sa.Column("created_by_user_id", sa.String(36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table("legal_entity", *_common(),
                    sa.Column("registered_address", sa.Text(), nullable=True),
                    sa.Column("authorised_signatory", sa.String(200), nullable=True))
    op.create_index("uq_legal_entity_org_name", "legal_entity", ["org_id", sa.text("lower(name)")], unique=True)
    op.create_table("counterparty", *_common(),
                    sa.Column("address", sa.Text(), nullable=True),
                    sa.Column("contact_email", sa.String(254), nullable=True))
    op.create_index("uq_counterparty_org_name", "counterparty", ["org_id", sa.text("lower(name)")], unique=True)
    op.add_column("intake_request", sa.Column(
        "counterparty_id", sa.String(36), sa.ForeignKey("counterparty.id", ondelete="SET NULL"), nullable=True))
    op.add_column("intake_request", sa.Column(
        "legal_entity_id", sa.String(36), sa.ForeignKey("legal_entity.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_intake_request_counterparty_id", "intake_request", ["counterparty_id"])
    op.create_index("ix_intake_request_legal_entity_id", "intake_request", ["legal_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_intake_request_legal_entity_id", table_name="intake_request")
    op.drop_index("ix_intake_request_counterparty_id", table_name="intake_request")
    op.drop_column("intake_request", "legal_entity_id")
    op.drop_column("intake_request", "counterparty_id")
    op.drop_table("counterparty")
    op.drop_table("legal_entity")
