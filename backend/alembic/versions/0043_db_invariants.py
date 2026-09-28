"""Two invariants the database now keeps itself.

One authoritative version per contract (several call sites kept this by
convention, so a race or a new caller could leave two), and job_run idempotency
keys unique per org — the index was global while jobs.service.create_job looks a
key up per org, so two orgs using the same key would collide with an
IntegrityError. Plain SQL (no inspection) so `alembic upgrade head --sql` still
works offline.

Revision ID: 0043_db_invariants
Revises: 0042_money_numeric
"""
from alembic import op

revision = "0043_db_invariants"
down_revision = "0042_money_numeric"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_version_authoritative "
        "ON contract_version (contract_id) WHERE is_authoritative AND deleted_at IS NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_job_run_idempotency_key")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_run_org_idempotency_key "
        "ON job_run (org_id, idempotency_key)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_run_idempotency_key ON job_run (idempotency_key)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_contract_version_authoritative")
    op.execute("DROP INDEX IF EXISTS uq_job_run_org_idempotency_key")
    op.execute("DROP INDEX IF EXISTS ix_job_run_idempotency_key")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_job_run_idempotency_key ON job_run (idempotency_key)")
