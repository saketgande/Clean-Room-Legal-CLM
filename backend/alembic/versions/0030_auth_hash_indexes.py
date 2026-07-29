"""Index RefreshToken.token_hash and ApiKey.key_hash.

Both are looked up by exact-match hash on every refresh/logout and every
API-key-authenticated request; without an index those were full table scans.
Uses CONCURRENTLY (via an autocommit block) so this doesn't lock the table on
a populated production database.
"""
from alembic import op

revision = "0030_auth_hash_indexes"
down_revision = "0029_intake_subject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_refresh_token_token_hash "
            "ON refresh_token (token_hash)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_api_key_key_hash "
            "ON api_key (key_hash)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_refresh_token_token_hash")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_api_key_key_hash")
