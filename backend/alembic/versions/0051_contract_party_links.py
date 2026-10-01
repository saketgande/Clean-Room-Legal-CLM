"""Link a contract to the counterparty and legal-entity records it was drafted for.

Contracts only carried a counterparty *name*, so a contract drafted from an
intake request lost the register records the request pointed at.
"""

import sqlalchemy as sa

from alembic import op

revision = "0051_contract_party_links"
down_revision = "0050_parties_master_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("contract", sa.Column(
        "counterparty_id", sa.String(36), sa.ForeignKey("counterparty.id", ondelete="SET NULL"), nullable=True))
    op.add_column("contract", sa.Column(
        "legal_entity_id", sa.String(36), sa.ForeignKey("legal_entity.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_contract_counterparty_id", "contract", ["counterparty_id"])
    op.create_index("ix_contract_legal_entity_id", "contract", ["legal_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_contract_legal_entity_id", table_name="contract")
    op.drop_index("ix_contract_counterparty_id", table_name="contract")
    op.drop_column("contract", "legal_entity_id")
    op.drop_column("contract", "counterparty_id")
