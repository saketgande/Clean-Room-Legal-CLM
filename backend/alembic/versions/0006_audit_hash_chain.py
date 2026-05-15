"""audit log hash chain

Revision ID: 0006_audit_hash_chain
Revises: 0005_schema_project_share
Create Date: 2026-05-15
"""

import hashlib
import json

from alembic import context, op
from sqlalchemy import text

revision = "0006_audit_hash_chain"
down_revision = "0005_schema_project_share"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _drop_audit_triggers()
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS prev_hash VARCHAR(64)")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS row_hash VARCHAR(64)")
    _backfill_hash_chain()
    op.execute("ALTER TABLE audit_log ALTER COLUMN row_hash SET NOT NULL")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_audit_log_row_hash ON audit_log (row_hash)")
    _create_audit_triggers()


def downgrade() -> None:
    _drop_audit_triggers()
    op.execute("DROP INDEX IF EXISTS ix_audit_log_row_hash")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS row_hash")
    op.execute("ALTER TABLE audit_log DROP COLUMN IF EXISTS prev_hash")
    _create_audit_triggers()


def _backfill_hash_chain() -> None:
    if context.is_offline_mode():
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM audit_log WHERE row_hash IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'audit_log hash backfill requires online Alembic migration for existing rows';
                END IF;
            END $$;
            """
        )
        return

    connection = op.get_bind()
    rows = connection.execute(
        text(
            """
            SELECT
                id,
                org_id,
                actor_user_id,
                action,
                resource_type,
                resource_id,
                request_id,
                ip_address,
                user_agent,
                before,
                after,
                metadata_json,
                created_at
            FROM audit_log
            ORDER BY created_at ASC, id ASC
            """
        )
    ).mappings()
    previous_hash = None
    for row in rows:
        row_hash = _audit_hash(row, previous_hash)
        connection.execute(
            text(
                """
                UPDATE audit_log
                SET prev_hash = :prev_hash,
                    row_hash = :row_hash
                WHERE id = :id
                """
            ),
            {"id": row["id"], "prev_hash": previous_hash, "row_hash": row_hash},
        )
        previous_hash = row_hash


def _audit_hash(row, previous_hash: str | None) -> str:  # noqa: ANN001
    created_at = row["created_at"]
    payload = {
        "id": row["id"],
        "org_id": row["org_id"],
        "actor_user_id": row["actor_user_id"],
        "action": row["action"],
        "resource_type": row["resource_type"],
        "resource_id": row["resource_id"],
        "request_id": row["request_id"],
        "ip_address": row["ip_address"],
        "user_agent": row["user_agent"],
        "before": row["before"],
        "after": row["after"],
        "metadata_json": row["metadata_json"],
        "prev_hash": previous_hash,
        "created_at": created_at.isoformat() if created_at else None,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _drop_audit_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log")


def _create_audit_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_update
        BEFORE UPDATE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_delete
        BEFORE DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation()
        """
    )
