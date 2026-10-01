"""Trademark comments: a flat, append-only discussion thread per trademark.

Adds the `trademark_comment` table — mirrors `contract_comment`'s shape
(0029_trademarks already established Trademark/DocumentExtract; this adds
the same comment pattern Contracts already has, minus the
negotiation-specific fields visibility/anchor/resolved_at that don't apply
to trademark discussion).

Revision ID: 0043_trademark_comments
Revises: 0042_trademark_intake_digest
"""

from alembic import op

revision = "0043_trademark_comments"
down_revision = "0042_trademark_intake_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS trademark_comment (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_at TIMESTAMP WITH TIME ZONE,
            deleted_by_user_id VARCHAR(36),
            legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
            trademark_id VARCHAR(36) NOT NULL REFERENCES trademark(id) ON DELETE CASCADE,
            parent_comment_id VARCHAR(36) REFERENCES trademark_comment(id),
            author_user_id VARCHAR(36) REFERENCES "user"(id),
            body TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_trademark_comment_org_id ON trademark_comment (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_trademark_comment_trademark_id ON trademark_comment (trademark_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trademark_comment CASCADE")
