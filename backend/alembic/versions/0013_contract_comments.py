"""contract comments (internal / shared, threaded, resolvable)

Revision ID: 0013_contract_comments
Revises: 0012_lean_lifecycle
Create Date: 2026-06-15
"""

from alembic import op

revision = "0013_contract_comments"
down_revision = "0012_lean_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS contract_comment (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            contract_id VARCHAR(36) NOT NULL REFERENCES contract(id),
            contract_version_id VARCHAR(36) REFERENCES contract_version(id),
            parent_comment_id VARCHAR(36) REFERENCES contract_comment(id),
            visibility VARCHAR(20) NOT NULL DEFAULT 'internal',
            author_kind VARCHAR(20) NOT NULL DEFAULT 'user',
            author_user_id VARCHAR(36) REFERENCES "user"(id),
            author_label VARCHAR(255),
            body TEXT NOT NULL,
            anchor JSON,
            mentioned_user_ids JSON NOT NULL DEFAULT '[]',
            resolved_at TIMESTAMP WITH TIME ZONE,
            resolved_by_user_id VARCHAR(36) REFERENCES "user"(id),
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_at TIMESTAMP WITH TIME ZONE,
            deleted_by_user_id VARCHAR(36),
            legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_contract_comment_org_id ON contract_comment (org_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contract_comment_contract_id ON contract_comment (contract_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contract_comment_visibility ON contract_comment (visibility)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contract_comment")
