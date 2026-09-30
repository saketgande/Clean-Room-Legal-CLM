"""Remove Tier-0 gates: approvers come only from a workflow's Approval step.

Gates were fixed in code (five keyword/AI rules) and silently added an
approver outside the workflow, raised priority and escalated the request.
Removed at the product owner's request (2026-09-28). This clears what they
stored on requests and any prompt override for their classifier. Irreversible
by design: the old detections are not restored on downgrade.
"""

from alembic import op

revision = "0055_drop_intake_gates"
down_revision = "0054_drop_approval_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE intake_request SET ai_triage = ai_triage - 'gates' - 'gate_overrides' "
        "WHERE ai_triage ?| array['gates', 'gate_overrides']"
    )
    op.execute("DELETE FROM a_i_prompt_version WHERE prompt_key = 'intake_gate_classifier'")


def downgrade() -> None:
    pass  # nothing to recreate: gates lived in code, not in a table
