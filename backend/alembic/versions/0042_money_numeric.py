"""Money as exact decimals: contract.value_amount and authority_grant.max_value.

Both were double precision, so authority checks at exactly a limit and portfolio
totals suffered binary rounding (e.g. 50000.000000000007). NUMERIC(18,2) stores
cents exactly. The USING clause rounds to 2 places; no existing contract value
needed rounding when this was written, and authority_grant was empty. Plain SQL
(no inspection) so `alembic upgrade head --sql` still works offline.

Revision ID: 0042_money_numeric
Revises: 0041_merge_heads
"""
from alembic import op

revision = "0042_money_numeric"
down_revision = "0041_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE contract ALTER COLUMN value_amount TYPE NUMERIC(18, 2) "
        "USING round(value_amount::numeric, 2)"
    )
    op.execute(
        "ALTER TABLE authority_grant ALTER COLUMN max_value TYPE NUMERIC(18, 2) "
        "USING round(max_value::numeric, 2)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE authority_grant ALTER COLUMN max_value TYPE DOUBLE PRECISION "
        "USING max_value::double precision"
    )
    op.execute(
        "ALTER TABLE contract ALTER COLUMN value_amount TYPE DOUBLE PRECISION "
        "USING value_amount::double precision"
    )
