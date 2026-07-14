"""Intake gap-fill: sanctions list, intake documents, screening column.

Raw idempotent SQL, matching 0022's conventions.
"""

from alembic import op

revision = "0023_intake_gaps"
down_revision = "0022_legal_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sanctions_list_entry (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            source VARCHAR(40) NOT NULL,
            source_ref VARCHAR(60) NOT NULL,
            name VARCHAR(400) NOT NULL,
            name_normalized VARCHAR(400) NOT NULL,
            programs VARCHAR(400),
            refreshed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_sanctions_source_ref
            ON sanctions_list_entry (source, source_ref);
        CREATE INDEX IF NOT EXISTS ix_sanctions_name_norm
            ON sanctions_list_entry (name_normalized);

        CREATE TABLE IF NOT EXISTS intake_document (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL,
            request_id VARCHAR(36) NOT NULL
                REFERENCES intake_request(id) ON DELETE CASCADE,
            filename VARCHAR(300) NOT NULL,
            mime_type VARCHAR(120) NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            extracted_text TEXT,
            extraction_quality FLOAT,
            created_by_user_id VARCHAR(36),
            updated_by_user_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_intake_document_request
            ON intake_document (request_id);

        ALTER TABLE intake_request ADD COLUMN IF NOT EXISTS screening JSON;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE intake_request DROP COLUMN IF EXISTS screening;
        DROP TABLE IF EXISTS intake_document;
        DROP TABLE IF EXISTS sanctions_list_entry;
        """
    )
