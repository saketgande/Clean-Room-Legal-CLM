"""production hardening: retention/dashboard indexes, token-hash uniqueness, gated RLS

Three classes of change, all additive and safe to run on a populated DB:

1. Time-range indexes on ``created_at`` for the high-volume log/event tables so
   future retention pruning ("delete rows older than N days") can scan an index
   instead of the heap.
2. Composite indexes matching the org-scoped dashboard list queries
   (filter by org + status, order/range by date).
3. Unique indexes on ``token_hash`` for the bearer-token tables that lacked one,
   closing a hash-collision / duplicate-issuance gap. ``password_reset_token``
   already had its unique index in 0001 and is left alone.

It also enables ROW LEVEL SECURITY (defense-in-depth) on the org-scoped sensitive
tables. The policy is intentionally PERMISSIVE when ``app.current_org_id`` is
unset, so the app keeps working whether or not it opts into setting that session
var (it only does when ``enable_rls`` is true). With the var set, a row is visible
only when its ``org_id`` matches.

Revision ID: 0009_prod_hardening_indexes_rls
Revises: 0008_revoked_access_token
Create Date: 2026-06-04
"""

from alembic import op

revision = "0009_prod_hardening_indexes_rls"
down_revision = "0008_revoked_access_token"
branch_labels = None
depends_on = None


# created_at indexes for retention pruning on the log/event tables.
_CREATED_AT_INDEXES = [
    ("ix_request_log_created_at", "request_log"),
    ("ix_audit_log_created_at", "audit_log"),
    ("ix_a_i_call_log_created_at", "a_i_call_log"),
    ("ix_resource_timeline_event_created_at", "resource_timeline_event"),
]

# (index_name, table, column_list) for the org-scoped dashboard queries.
_COMPOSITE_INDEXES = [
    ("ix_obligation_org_id_status_due_date", "obligation", "org_id, status, due_date"),
    ("ix_approval_request_org_id_status_due_at", "approval_request", "org_id, status, due_at"),
    ("ix_signature_request_org_id_status", "signature_request", "org_id, status"),
    ("ix_playbook_run_org_id_status", "playbook_run", "org_id, status"),
]

# token_hash tables that lacked a unique index (password_reset_token already has one).
_TOKEN_HASH_UNIQUE_INDEXES = [
    ("ix_refresh_token_token_hash", "refresh_token"),
    ("ix_user_invitation_token_hash", "user_invitation"),
    ("ix_approval_token_token_hash", "approval_token"),
    ("ix_contract_share_token_hash", "contract_share"),
]

# Sensitive, org-scoped tables that get RLS. Every table here has an org_id column.
_RLS_TABLES = [
    "contract",
    "contract_version",
    "contract_text_snapshot",
    "obligation",
    "approval_request",
    "audit_log",
    "admin_setting",
]


def _rls_policy_name(table: str) -> str:
    return f"{table}_org_isolation"


def upgrade() -> None:
    for index_name, table in _CREATED_AT_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} (created_at)")

    for index_name, table, columns in _COMPOSITE_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({columns})")

    for index_name, table in _TOKEN_HASH_UNIQUE_INDEXES:
        op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} (token_hash)")

    # Defense-in-depth org isolation. Permissive when app.current_org_id is unset so
    # local/dev/test and any code path that doesn't set the var keeps full access;
    # the app sets the var only when enable_rls=true. FORCE makes policies apply
    # even if the app connects as the table owner.
    for table in _RLS_TABLES:
        policy = _rls_policy_name(table)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(
            f"""
            CREATE POLICY {policy} ON {table}
            USING (
                current_setting('app.current_org_id', true) IS NULL
                OR org_id::text = current_setting('app.current_org_id', true)
            )
            """
        )


def downgrade() -> None:
    for table in _RLS_TABLES:
        policy = _rls_policy_name(table)
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for index_name, _table in _TOKEN_HASH_UNIQUE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    for index_name, _table, _columns in _COMPOSITE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    for index_name, _table in _CREATED_AT_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
