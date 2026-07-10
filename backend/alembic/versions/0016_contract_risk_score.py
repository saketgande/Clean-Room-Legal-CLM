"""weighted, explainable contract risk score

Adds a computed risk score (0-100), a band, and a JSON summary holding the
per-clause drivers (weight x severity + rationale) behind the score — so the
number is explainable, not a magic badge.

Revision ID: 0016_contract_risk_score
Revises: 0015_full_text_search
Create Date: 2026-07-08
"""

from alembic import op

revision = "0016_contract_risk_score"
down_revision = "0015_full_text_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE contract ADD COLUMN IF NOT EXISTS risk_score INTEGER")
    op.execute("ALTER TABLE contract ADD COLUMN IF NOT EXISTS risk_band VARCHAR(40)")
    op.execute("ALTER TABLE contract ADD COLUMN IF NOT EXISTS risk_summary JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE contract DROP COLUMN IF EXISTS risk_summary")
    op.execute("ALTER TABLE contract DROP COLUMN IF EXISTS risk_band")
    op.execute("ALTER TABLE contract DROP COLUMN IF EXISTS risk_score")
