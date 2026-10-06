"""Trademark Suite integration: link a trademark back to the Legal Intake
request it was continued from, plus the per-org portfolio digest table.

`source_intake_request_id` is the same deliberate choice already made for
`notice.escalated_intake_request_id` (see 0035_notice_response_escalation):
a trademark that started as a Legal Intake request doesn't get its own
bespoke triage/routing — it reuses the intake ticket's existing routing,
SLA clock and approval ladder. The trademark stays the system of record for
the mark itself; the intake ticket is (or was) the unit of work the legal
team triaged. ON DELETE SET NULL, not CASCADE: deleting the intake ticket
must never take the trademark record down with it.

`portfolio_digest` is one row per org per calendar day, holding the one
LLM-generated summary of what changed in that org's trademark portfolio
that day. Unlike the standalone module this was ported from (which used a
single global `digest_date` primary key — see backend/TRADEMARKS_INTEGRATION.md),
this is per-org from the start: Aegis is multi-tenant everywhere, and a
global digest would leak one org's portfolio activity into every other
org's dashboard. The (org_id, digest_date) uniqueness is the idempotency
guard: generating "today's" digest is a get-or-create on that pair, so it
never costs more than one LLM call per org per day no matter how many times
it's requested.

Revision ID: 0042_trademark_intake_digest
Revises: 0041_merge_heads
"""
from alembic import op

revision = "0042_trademark_intake_digest"
down_revision = "0041_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE trademark
        ADD COLUMN IF NOT EXISTS source_intake_request_id VARCHAR(36)
        REFERENCES intake_request(id) ON DELETE SET NULL;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_trademark_source_intake_request_id "
        "ON trademark (source_intake_request_id);"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_digest (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            digest_date DATE NOT NULL,
            summary TEXT NOT NULL,
            stats JSON NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_portfolio_digest_org_id ON portfolio_digest (org_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_portfolio_digest_org_id_digest_date "
        "ON portfolio_digest (org_id, digest_date)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS portfolio_digest CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_trademark_source_intake_request_id")
    op.execute("ALTER TABLE trademark DROP COLUMN IF EXISTS source_intake_request_id")
