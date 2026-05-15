"""deployable schema and project shares

Revision ID: 0005_schema_project_share
Revises: 0004_phase1_auth_foundation
Create Date: 2026-05-15
"""

from alembic import op

revision = "0005_schema_project_share"
down_revision = "0004_phase1_auth_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_token (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(36) NOT NULL REFERENCES "user"(id),
            token_hash VARCHAR(255) NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            revoked_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_refresh_token_org_id ON refresh_token (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_refresh_token_user_id ON refresh_token (user_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS api_key (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            user_id VARCHAR(36) NOT NULL REFERENCES "user"(id),
            name VARCHAR(255) NOT NULL,
            key_hash VARCHAR(255) NOT NULL,
            last_used_at TIMESTAMP WITH TIME ZONE,
            revoked_at TIMESTAMP WITH TIME ZONE,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_api_key_org_id ON api_key (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_api_key_user_id ON api_key (user_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_invitation (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            email VARCHAR(320) NOT NULL,
            role_name VARCHAR(120) NOT NULL,
            token_hash VARCHAR(255) NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            accepted_at TIMESTAMP WITH TIME ZONE,
            revoked_at TIMESTAMP WITH TIME ZONE,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_invitation_org_id ON user_invitation (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_user_invitation_email ON user_invitation (email)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_join_request (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) REFERENCES organization(id),
            email VARCHAR(320) NOT NULL,
            full_name VARCHAR(255) NOT NULL,
            requested_domain VARCHAR(255),
            message TEXT,
            status VARCHAR(40) NOT NULL,
            decided_by_user_id VARCHAR(36) REFERENCES "user"(id),
            decision_reason TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_join_request_org_id ON org_join_request (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_join_request_email ON org_join_request (email)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_join_request_requested_domain "
        "ON org_join_request (requested_domain)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_approval_decision (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            target_user_id VARCHAR(36) NOT NULL REFERENCES "user"(id),
            decision VARCHAR(40) NOT NULL,
            reason TEXT,
            metadata_json JSON,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_approval_decision_org_id "
        "ON user_approval_decision (org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_approval_decision_target_user_id "
        "ON user_approval_decision (target_user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_approval_decision_decision "
        "ON user_approval_decision (decision)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_share (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            project_id VARCHAR(36) NOT NULL REFERENCES project(id),
            shared_with_user_id VARCHAR(36) NOT NULL REFERENCES "user"(id),
            access_level VARCHAR(40) NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE,
            revoked_at TIMESTAMP WITH TIME ZONE,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_project_share_org_id ON project_share (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_project_share_project_id ON project_share (project_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_share_shared_with_user_id "
        "ON project_share (shared_with_user_id)"
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_contract_embedding_embedding_hnsw
        ON contract_embedding USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contract_embedding_embedding_hnsw")
    op.execute("DROP TABLE IF EXISTS project_share CASCADE")
    op.execute("DROP TABLE IF EXISTS user_approval_decision CASCADE")
    op.execute("DROP TABLE IF EXISTS org_join_request CASCADE")
    op.execute("DROP TABLE IF EXISTS user_invitation CASCADE")
    op.execute("DROP TABLE IF EXISTS api_key CASCADE")
    op.execute("DROP TABLE IF EXISTS refresh_token CASCADE")
