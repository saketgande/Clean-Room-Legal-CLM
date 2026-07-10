"""drop row-level-security policies (single-tenant deployment)

Aegis is deployed single-tenant — one company per install — so the Postgres
row-level-security tenant floor added in 0009 has no purpose (there is no
second tenant to isolate from) and was never actually wired (set_session_org
had no callers, so the USING() predicate always evaluated permissively).

This drops the org-isolation policies and disables RLS on the 7 tables. It does
NOT touch any index created in 0009 — the retention/dashboard and token-hash
unique indexes are load-bearing and stay. Safe and reversible.

Revision ID: 0018_drop_rls_single_tenant
Revises: 0017_workflow_versioning
Create Date: 2026-07-08
"""

from alembic import op

revision = "0018_drop_rls_single_tenant"
down_revision = "0017_workflow_versioning"
branch_labels = None
depends_on = None

_RLS_TABLES = [
    "contract",
    "contract_version",
    "contract_text_snapshot",
    "obligation",
    "approval_request",
    "audit_log",
    "admin_setting",
]


def upgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Re-create the permissive org-isolation policies exactly as 0009 did.
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_org_isolation ON {table}
            USING (
                current_setting('app.current_org_id', true) IS NULL
                OR org_id = current_setting('app.current_org_id', true)
            )
            """
        )
