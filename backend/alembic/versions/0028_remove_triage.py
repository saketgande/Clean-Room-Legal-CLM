"""Remove the intake triage experience.

Drops the AI-recommendation table + the agent-tracking columns, collapses the
old awaiting_triage/in_review statuses into a single 'open', and removes the
now-unused intake:triage permission. The plumbing (Tier-0 gates, routing,
screening, flow suggestion, SLA/handoff ledger, approval-lock) is kept.

One-way: the recommendation data is not restorable, so downgrade is a no-op.
"""
from alembic import op

revision = "0028_remove_triage"
down_revision = "0027_approval_quorum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Handoff FK references the recommendation table — drop it before the table.
    op.execute("ALTER TABLE intake_handoff DROP COLUMN IF EXISTS recommendation_id;")
    op.execute("DROP TABLE IF EXISTS intake_agent_recommendation;")
    # AI-agent tracking columns on the request.
    op.execute("ALTER TABLE intake_request DROP COLUMN IF EXISTS agent_processed_at;")
    op.execute("ALTER TABLE intake_request DROP COLUMN IF EXISTS agent_outcome;")
    # Collapse the two open statuses (nobody-looked / being-worked) into 'open'.
    op.execute(
        "UPDATE intake_request SET status='open' "
        "WHERE status IN ('awaiting_triage','in_review');"
    )
    # Remove the intake:triage permission and any role links to it.
    op.execute(
        "DELETE FROM role_permission WHERE permission_id IN "
        "(SELECT id FROM permission WHERE value='intake:triage');"
    )
    op.execute("DELETE FROM permission WHERE value='intake:triage';")


def downgrade() -> None:
    # One-way teardown — the removed data cannot be reconstructed.
    pass
