"""Merge heads: trademarks module + org_join_request drop.

0029_trademarks and 0029_intake_subject were both authored against
0028_remove_triage on separate branches, so the merge into drl-akhil left
two independent head chains (ending at 0029_trademarks and
0031_drop_org_join_request respectively). This is a no-op merge revision
to bring the migration graph back to a single head.

Revision ID: 0032_merge_heads
Revises: 0029_trademarks, 0031_drop_org_join_request
"""

revision = "0032_merge_heads"
down_revision = ("0029_trademarks", "0031_drop_org_join_request")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
