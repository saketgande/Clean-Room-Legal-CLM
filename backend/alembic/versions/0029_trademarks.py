"""Trademarks module: portfolio records + document-extraction pipeline.

Adds the `trademark` and `document_extract` tables backing the new
Trademarks module (intake, similarity search, document extraction). Both
carry a 384-dim pgvector embedding column (matching the existing local
bge-small embedding pipeline in app/ai/embeddings.py) with an HNSW cosine
index for the internal-portfolio similarity search source.

Revision ID: 0029_trademarks
Revises: 0028_remove_triage
"""

from alembic import op

revision = "0029_trademarks"
down_revision = "0028_remove_triage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS trademark (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            deleted_at TIMESTAMP WITH TIME ZONE,
            deleted_by_user_id VARCHAR(36),
            legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            status VARCHAR(80) NOT NULL DEFAULT 'draft',
            trademark_type VARCHAR(40) NOT NULL DEFAULT 'word_mark',
            jurisdiction VARCHAR(120) NOT NULL,
            jurisdictions JSON,
            nice_class VARCHAR(255),
            goods_services TEXT,
            filing_context JSON,
            filed_on TIMESTAMP WITH TIME ZONE,
            renewal_due_on TIMESTAMP WITH TIME ZONE,
            workflow_state VARCHAR(80) NOT NULL DEFAULT 'intake',
            source VARCHAR(40) NOT NULL DEFAULT 'intake',
            source_document_extract_id VARCHAR(36),
            embedding_text TEXT,
            embedding VECTOR(384),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_trademark_org_id ON trademark (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_trademark_status ON trademark (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_trademark_name ON trademark (name)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_trademark_renewal_due_on ON trademark (renewal_due_on)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS document_extract (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            source_filename VARCHAR(500) NOT NULL,
            storage_key VARCHAR(500),
            page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL,
            template VARCHAR(40) NOT NULL DEFAULT 'generic',
            field_schema JSON,
            extracted_fields JSON NOT NULL,
            embedding_text TEXT,
            embedding VECTOR(384),
            ingested_trademark_id VARCHAR(36),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_document_extract_org_id ON document_extract (org_id)")

    # Cross-referencing FKs added after both tables exist.
    op.execute(
        "ALTER TABLE trademark ADD CONSTRAINT fk_trademark_source_document_extract_id_document_extract "
        "FOREIGN KEY (source_document_extract_id) REFERENCES document_extract(id)"
    )
    op.execute(
        "ALTER TABLE document_extract ADD CONSTRAINT fk_document_extract_ingested_trademark_id_trademark "
        "FOREIGN KEY (ingested_trademark_id) REFERENCES trademark(id)"
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_trademark_embedding_hnsw
        ON trademark USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_trademark_embedding_hnsw")
    op.execute(
        "ALTER TABLE document_extract DROP CONSTRAINT IF EXISTS "
        "fk_document_extract_ingested_trademark_id_trademark"
    )
    op.execute(
        "ALTER TABLE trademark DROP CONSTRAINT IF EXISTS "
        "fk_trademark_source_document_extract_id_document_extract"
    )
    op.execute("DROP TABLE IF EXISTS document_extract CASCADE")
    op.execute("DROP TABLE IF EXISTS trademark CASCADE")
