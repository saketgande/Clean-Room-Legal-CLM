"""Document extraction ingests into document_extract only, and is searched there.

Ingesting an extraction no longer creates a ``trademark`` row, so the two link
columns that tied a trademark to its extraction have nothing left to record:
``trademark.source_document_extract_id`` and
``document_extract.ingested_trademark_id`` are dropped (both were always NULL
once ingest stopped linking). The similarity search now reads
``document_extract.embedding`` directly, so it gets the same HNSW cosine index
``trademark.embedding`` already has (0029_trademarks).

Revision ID: 0050_extract_only_ingest
Revises: 0049_trademark_menu_labels
"""

from alembic import op

revision = "0050_extract_only_ingest"
down_revision = "0049_trademark_menu_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE trademark DROP COLUMN IF EXISTS source_document_extract_id")
    op.execute("ALTER TABLE document_extract DROP COLUMN IF EXISTS ingested_trademark_id")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_document_extract_embedding_hnsw
        ON document_extract USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_extract_embedding_hnsw")
    op.execute("ALTER TABLE document_extract ADD COLUMN IF NOT EXISTS ingested_trademark_id VARCHAR(36)")
    op.execute(
        "ALTER TABLE document_extract ADD CONSTRAINT fk_document_extract_ingested_trademark_id_trademark "
        "FOREIGN KEY (ingested_trademark_id) REFERENCES trademark(id)"
    )
    op.execute("ALTER TABLE trademark ADD COLUMN IF NOT EXISTS source_document_extract_id VARCHAR(36)")
    op.execute(
        "ALTER TABLE trademark ADD CONSTRAINT fk_trademark_source_document_extract_id_document_extract "
        "FOREIGN KEY (source_document_extract_id) REFERENCES document_extract(id)"
    )
