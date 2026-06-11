"""phase 2 hardening

Revision ID: 0003_phase2_hardening
Revises: 0002_ai_architecture_spine
Create Date: 2026-05-15
"""

from uuid import uuid4

from alembic import op

revision = "0003_phase2_hardening"
down_revision = "0002_ai_architecture_spine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        """
        UPDATE contract
        SET current_contract_file_id = NULL
        WHERE current_contract_file_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM contract_file
            WHERE contract_file.id = contract.current_contract_file_id
          )
        """
    )
    op.execute(
        """
        UPDATE contract
        SET current_authoritative_version_id = NULL
        WHERE current_authoritative_version_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM contract_version
            WHERE contract_version.id = contract.current_authoritative_version_id
          )
        """
    )
    op.execute(
        """
        UPDATE contract_file
        SET current_version_id = NULL
        WHERE current_version_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM contract_version
            WHERE contract_version.id = contract_file.current_version_id
          )
        """
    )
    op.execute(
        """
        UPDATE contract_version
        SET text_snapshot_id = NULL
        WHERE text_snapshot_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM contract_text_snapshot
            WHERE contract_text_snapshot.id = contract_version.text_snapshot_id
          )
        """
    )

    for constraint_sql in [
        """
        ALTER TABLE contract
        ADD CONSTRAINT fk_contract_current_contract_file_id
        FOREIGN KEY (current_contract_file_id) REFERENCES contract_file(id)
        """,
        """
        ALTER TABLE contract
        ADD CONSTRAINT fk_contract_current_authoritative_version_id
        FOREIGN KEY (current_authoritative_version_id) REFERENCES contract_version(id)
        """,
        """
        ALTER TABLE contract_file
        ADD CONSTRAINT fk_contract_file_current_version_id
        FOREIGN KEY (current_version_id) REFERENCES contract_version(id)
        """,
        """
        ALTER TABLE contract_version
        ADD CONSTRAINT fk_contract_version_text_snapshot_id
        FOREIGN KEY (text_snapshot_id) REFERENCES contract_text_snapshot(id)
        """,
    ]:
        constraint_name = constraint_sql.split("ADD CONSTRAINT ", 1)[1].split()[0]
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = '{constraint_name}'
                ) THEN
                    {constraint_sql};
                END IF;
            END $$;
            """
        )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contract_text_snapshot_text_trgm "
        "ON contract_text_snapshot USING gin (text gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clause_extraction_text_trgm "
        "ON clause_extraction USING gin (text gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clause_extraction_heading_trgm "
        "ON clause_extraction USING gin (heading gin_trgm_ops)"
    )

    permission_id = str(uuid4())
    op.execute(
        f"""
        INSERT INTO permission (id, value, description, created_at, updated_at)
        SELECT
            '{permission_id}',
            'project:delete',
            'Delete projects',
            NOW(),
            NOW()
        WHERE NOT EXISTS (
            SELECT 1 FROM permission WHERE value = 'project:delete'
        )
        """
    )
    op.execute(
        """
        INSERT INTO role_permission (role_id, permission_id)
        SELECT role.id, permission.id
        FROM role
        JOIN permission ON permission.value = 'project:delete'
        WHERE role.name = 'admin'
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permission
        USING permission
        WHERE role_permission.permission_id = permission.id
          AND permission.value = 'project:delete'
        """
    )
    op.execute("DELETE FROM permission WHERE value = 'project:delete'")
    op.execute("DROP INDEX IF EXISTS ix_clause_extraction_heading_trgm")
    op.execute("DROP INDEX IF EXISTS ix_clause_extraction_text_trgm")
    op.execute("DROP INDEX IF EXISTS ix_contract_text_snapshot_text_trgm")
    for constraint_name, table_name in [
        ("fk_contract_version_text_snapshot_id", "contract_version"),
        ("fk_contract_file_current_version_id", "contract_file"),
        ("fk_contract_current_authoritative_version_id", "contract"),
        ("fk_contract_current_contract_file_id", "contract"),
    ]:
        op.execute(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {constraint_name}")
