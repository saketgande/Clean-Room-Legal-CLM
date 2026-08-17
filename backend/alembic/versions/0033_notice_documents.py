"""Attachments on a notice.

Mirrors intake_document: metadata plus the *extracted text*, not the bytes.
The text is what the extraction agent and any later search actually read, and
keeping blobs out of Postgres avoids a second storage lifecycle for what is
usually a two-page PDF.
"""
from alembic import op

revision = "0033_notice_documents"
down_revision = "0032_notice_register"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notice_document (
            id                  VARCHAR(36)  PRIMARY KEY,
            org_id              VARCHAR(36)  NOT NULL,
            notice_id           VARCHAR(36)  NOT NULL REFERENCES notice(id) ON DELETE CASCADE,
            filename            VARCHAR(300) NOT NULL,
            mime_type           VARCHAR(120) NOT NULL,
            size_bytes          INTEGER      NOT NULL DEFAULT 0,
            extracted_text      TEXT,
            extraction_quality  DOUBLE PRECISION,
            created_by_user_id  VARCHAR(36),
            updated_by_user_id  VARCHAR(36),
            created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_notice_document_org_id ON notice_document (org_id);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notice_document_notice_id "
        "ON notice_document (notice_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notice_document;")
