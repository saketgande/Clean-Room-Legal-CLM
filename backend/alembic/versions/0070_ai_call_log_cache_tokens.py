"""Record prompt-cache tokens on the AI call ledger.

Anthropic reports cache writes and cache reads separately from input_tokens,
and they're billed at different rates (1.25x and 0.1x input). The ledger kept
only input/output tokens, so the AI Usage & Cost page couldn't price cached
calls. Two nullable columns, written by the AI gateway's ledger.

Additive only: no backfill (the numbers were never stored), no default, and
existing rows stay NULL. Downgrade drops the two columns.

Revision ID: 0070_ai_call_log_cache_tokens
Revises: 0069_pin_general_legal_question
"""

import sqlalchemy as sa

from alembic import op

revision = "0070_ai_call_log_cache_tokens"
down_revision = "0069_pin_general_legal_question"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("a_i_call_log", sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=True))
    op.add_column("a_i_call_log", sa.Column("cache_read_input_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("a_i_call_log", "cache_read_input_tokens")
    op.drop_column("a_i_call_log", "cache_creation_input_tokens")
