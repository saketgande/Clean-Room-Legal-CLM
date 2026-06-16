"""collapse contract lifecycle to 7 stages; renewal_due/archived become flags

Revision ID: 0012_lean_lifecycle
Revises: 0011_approval_groups_and_steps
Create Date: 2026-06-15

Stage remap (old -> new):
    ai_review/internal_review/counterparty_review -> review
    approval_pending                              -> approval
    approved/signature_pending                    -> signature
    renewal_due                                   -> active   (+ renewal_due flag)
    archived                                      -> closed   (+ archived flag)
"""

from alembic import op

revision = "0012_lean_lifecycle"
down_revision = "0011_approval_groups_and_steps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. New boolean flags (folded out of the stage enum).
    op.execute("ALTER TABLE contract ADD COLUMN IF NOT EXISTS renewal_due BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE contract ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT FALSE")

    # 2. Set flags from the old stages BEFORE remapping them away.
    op.execute("UPDATE contract SET renewal_due = TRUE WHERE lifecycle_stage = 'renewal_due'")
    op.execute("UPDATE contract SET archived = TRUE WHERE lifecycle_stage = 'archived'")

    # 3. Remap contract stages to the lean set.
    op.execute(
        "UPDATE contract SET lifecycle_stage = 'review' "
        "WHERE lifecycle_stage IN ('ai_review', 'internal_review', 'counterparty_review')"
    )
    op.execute("UPDATE contract SET lifecycle_stage = 'approval' WHERE lifecycle_stage = 'approval_pending'")
    op.execute(
        "UPDATE contract SET lifecycle_stage = 'signature' "
        "WHERE lifecycle_stage IN ('approved', 'signature_pending')"
    )
    op.execute("UPDATE contract SET lifecycle_stage = 'active' WHERE lifecycle_stage = 'renewal_due'")
    op.execute("UPDATE contract SET lifecycle_stage = 'closed' WHERE lifecycle_stage = 'archived'")

    op.execute("CREATE INDEX IF NOT EXISTS ix_contract_renewal_due ON contract (renewal_due)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_contract_archived ON contract (archived)")

    # 4. Remap the stage-history rows too, so timelines/metrics don't show dead
    #    stage names (cycle-time keys off to_stage = 'active', which is unchanged).
    for col in ("to_stage", "from_stage"):
        op.execute(
            f"UPDATE contract_stage_history SET {col} = 'review' "
            f"WHERE {col} IN ('ai_review', 'internal_review', 'counterparty_review')"
        )
        op.execute(f"UPDATE contract_stage_history SET {col} = 'approval' WHERE {col} = 'approval_pending'")
        op.execute(
            f"UPDATE contract_stage_history SET {col} = 'signature' "
            f"WHERE {col} IN ('approved', 'signature_pending')"
        )
        op.execute(f"UPDATE contract_stage_history SET {col} = 'active' WHERE {col} = 'renewal_due'")
        op.execute(f"UPDATE contract_stage_history SET {col} = 'closed' WHERE {col} = 'archived'")


def downgrade() -> None:
    # Lossy: the merged stages can't be perfectly restored. Re-derive what we can
    # from the flags, then drop the flag columns.
    op.execute("UPDATE contract SET lifecycle_stage = 'renewal_due' WHERE renewal_due = TRUE")
    op.execute("UPDATE contract SET lifecycle_stage = 'archived' WHERE archived = TRUE")
    op.execute("DROP INDEX IF EXISTS ix_contract_archived")
    op.execute("DROP INDEX IF EXISTS ix_contract_renewal_due")
    op.execute("ALTER TABLE contract DROP COLUMN IF EXISTS archived")
    op.execute("ALTER TABLE contract DROP COLUMN IF EXISTS renewal_due")
