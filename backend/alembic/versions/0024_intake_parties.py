"""Intake conflict/COI: structured parties on a request.

`parties` is a JSON list of {name, role, is_person} — the counterparty plus any
adverse / related parties. It powers the deepened conflict-of-interest check and
counterparty relationship enrichment (screening.py). Kept as JSON (not a table)
because parties are edited with the request and queried per-request, not indexed.
"""

from alembic import op

revision = "0024_intake_parties"
down_revision = "0023_intake_gaps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE intake_request ADD COLUMN IF NOT EXISTS parties JSON;")


def downgrade() -> None:
    op.execute("ALTER TABLE intake_request DROP COLUMN IF EXISTS parties;")
