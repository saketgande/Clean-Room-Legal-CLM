"""Merge heads: trademark-suite line + org-hierarchy / menu-security line.

Merging drl into feature/dependency-injection brought together two chains that
both fork off 0041_merge_heads: 0042_trademark_intake_digest ->
0043_trademark_comments (trademark intake link, portfolio digest, discussion
threads) and 0042_org_hierarchy_rbac -> ... -> 0046_contract_share_step_link
(org hierarchy, menu/screen security, approval chains). Neither chain depends
on the other. This is a no-op merge revision that brings the migration graph
back to a single head, same as 0041_merge_heads / 0032_merge_heads before it.

Revision ID: 0047_merge_heads
Revises: 0043_trademark_comments, 0046_contract_share_step_link
"""

revision = "0047_merge_heads"
down_revision = ("0043_trademark_comments", "0046_contract_share_step_link")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
