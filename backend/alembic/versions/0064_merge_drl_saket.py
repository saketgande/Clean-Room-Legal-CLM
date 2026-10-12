"""Merge drl's approval-chains / menu-security branch with drl-saket's line.

Both branches grew from 0041_merge_heads: drl added org hierarchy, menu/screen
security and approval chains (0042–0046); drl-saket added docstudio, intake
forms, parties, teams, workflow stages, Matters removal and revision rounds
(0042–0063). Neither side alters a table the other creates, so this is a pure
join with no schema change.

Revision ID: 0064_merge_drl_saket
Revises: 0063_revision_rounds, 0046_contract_share_step_link
"""

revision = "0064_merge_drl_saket"
down_revision = ("0063_revision_rounds", "0046_contract_share_step_link")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
