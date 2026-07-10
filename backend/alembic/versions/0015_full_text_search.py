"""full-text search: GIN expression indexes for contract text + clauses

The /search endpoints move from ILIKE '%…%' scans to Postgres full-text
(websearch_to_tsquery + ts_rank). These expression indexes must match the
expressions used in app/search/routes.py exactly, including the left(…) cap
(tsvectors have a 1 MB limit, and some contract texts are multi-megabyte).

Revision ID: 0015_full_text_search
Revises: 0014_notification_read_state
Create Date: 2026-07-07
"""

from alembic import op

revision = "0015_full_text_search"
down_revision = "0014_notification_read_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_contract_text_snapshot_fts
        ON contract_text_snapshot
        USING GIN (to_tsvector('english', left(text, 200000)))
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_clause_extraction_fts
        ON clause_extraction
        USING GIN (to_tsvector('english', coalesce(heading, '') || ' ' || left(text, 200000)))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contract_text_snapshot_fts")
    op.execute("DROP INDEX IF EXISTS ix_clause_extraction_fts")
