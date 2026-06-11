"""critical integrity fixes for versions, handles, and idempotency

Revision ID: 0010_critical_integrity_fixes
Revises: 0009_prod_hardening_indexes_rls
Create Date: 2026-06-10
"""

from alembic import op

revision = "0010_critical_integrity_fixes"
down_revision = "0009_prod_hardening_indexes_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_version_file_number
        ON contract_version (contract_file_id, version_number)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_assistant_contract_handle_session_handle
        ON assistant_contract_handle (session_id, handle)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_assistant_contract_handle_session_contract
        ON assistant_contract_handle (session_id, contract_id)
        """
    )


def downgrade() -> None:
    for index_name in (
        "uq_assistant_contract_handle_session_contract",
        "uq_assistant_contract_handle_session_handle",
        "uq_contract_version_file_number",
    ):
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
