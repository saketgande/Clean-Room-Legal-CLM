"""Approval ladder generalised beyond contracts.

The approval-chain engine (app/approvals) now drives Legal Intake requests as
well as contracts, so an ``ApprovalRequest`` hangs off EITHER a contract or an
intake request. ``contract_id`` becomes nullable and a sibling
``intake_request_id`` is added; exactly one of the two is set per row.
"""

from alembic import op

revision = "0025_approval_subject"
down_revision = "0024_intake_parties"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # contract_id no longer mandatory — an intake-request approval leaves it NULL.
    op.execute("ALTER TABLE approval_request ALTER COLUMN contract_id DROP NOT NULL;")
    op.execute(
        "ALTER TABLE approval_request ADD COLUMN IF NOT EXISTS intake_request_id VARCHAR(36);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_request_intake_request_id "
        "ON approval_request (intake_request_id);"
    )
    op.execute(
        """
        DO $$ BEGIN
          ALTER TABLE approval_request
            ADD CONSTRAINT fk_approval_request_intake
            FOREIGN KEY (intake_request_id) REFERENCES intake_request(id)
            ON DELETE CASCADE;
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE approval_request DROP CONSTRAINT IF EXISTS fk_approval_request_intake;"
    )
    op.execute("DROP INDEX IF EXISTS ix_approval_request_intake_request_id;")
    op.execute("ALTER TABLE approval_request DROP COLUMN IF EXISTS intake_request_id;")
    # contract_id is left nullable on downgrade — re-adding NOT NULL could fail if
    # intake-request approval rows exist.
