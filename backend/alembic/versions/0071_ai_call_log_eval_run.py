"""Tag AI exam traffic on the AI call ledger.

The evaluation exam (feature 005) makes real model calls and copies their
metering rows onto the live ledger. Two columns keep that spend honest:
`eval_run_id` marks a row as exam traffic of that run (NULL = real usage), so
the AI Usage & Cost page can leave it out of the real figures; `batched` marks
calls answered through the Message Batches API, which the provider bills at 50%.

Additive only: no backfill (every existing row is real, unbatched usage). The
boolean's constant default is metadata-only on Postgres 11+, so existing rows
read false without a table rewrite. No FK on eval_run_id; the run record lives
in audit_log. Downgrade drops the columns and the index.

Revision ID: 0071_ai_call_log_eval_run
Revises: 0070_ai_call_log_cache_tokens
"""

import sqlalchemy as sa

from alembic import op

revision = "0071_ai_call_log_eval_run"
down_revision = "0070_ai_call_log_cache_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("a_i_call_log", sa.Column("eval_run_id", sa.String(36), nullable=True))
    op.create_index("ix_a_i_call_log_eval_run_id", "a_i_call_log", ["eval_run_id"])
    op.add_column(
        "a_i_call_log",
        sa.Column("batched", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("a_i_call_log", "batched")
    op.drop_index("ix_a_i_call_log_eval_run_id", table_name="a_i_call_log")
    op.drop_column("a_i_call_log", "eval_run_id")
