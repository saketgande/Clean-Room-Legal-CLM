"""Alembic migration backing CC-2: UNIQUE(session_id, handle) on assistant_contract_handle.

Intended filename when merged: ``backend/alembic/versions/0009_assistant_contract_handle_unique.py``.

Adds the unique index that backstops ``allocate_contract_handle`` in
``app/ai/session_state.py`` (CC-2). Without this index, two concurrent
workers can race past the ``SELECT count(*)`` and both insert
``contract-N`` for the same session — exactly the bug Agent 2 F-09
called out.

Run a one-shot cleanup BEFORE upgrade to remove any pre-existing
duplicate rows (keeping the lowest ``id``). See the function docstring
below.

Revision ID: 0009_assistant_contract_handle_unique
Revises: 0008_revoked_access_token
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0009_assistant_contract_handle_unique"
down_revision = "0008_revoked_access_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create UNIQUE(session_id, handle) after pruning duplicates.

    Duplicate pruning keeps the lowest ``id`` per ``(session_id, handle)``
    pair on the assumption that earlier rows have the most history
    (citations, tool-call references) bound to them. Adjust if your
    deployment cares more about ``contract_id`` lineage.
    """
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    if dialect_name == "postgresql":
        bind.execute(
            sa.text(
                """
                DELETE FROM assistant_contract_handle
                WHERE id IN (
                    SELECT id
                    FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY session_id, handle
                                   ORDER BY created_at ASC, id ASC
                               ) AS row_num
                        FROM assistant_contract_handle
                    ) ranked
                    WHERE row_num > 1
                )
                """
            )
        )
    op.create_index(
        "ux_assistant_contract_handle_session_handle",
        "assistant_contract_handle",
        ["session_id", "handle"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ux_assistant_contract_handle_session_handle",
        table_name="assistant_contract_handle",
    )
