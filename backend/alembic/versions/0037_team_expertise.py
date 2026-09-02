"""Team expertise + business unit for context-aware owner assignment.

`expertise` is the list of matter categories a team owns (e.g. ["Privacy",
"Litigation"]); `business_unit` optionally scopes the team to a BU. The triage
routes a request's owner to the team whose expertise covers the matter category
(narrowed by business unit when both declare one), load-balanced within.

Back-fills the seeded tiers so existing orgs route by expertise immediately:
tier1 (paralegals) → lighter matters, tier2 (counsel) → the rest.
"""
import sqlalchemy as sa
from alembic import op

revision = "0037_team_expertise"
down_revision = "0036_workflow_rename"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("intake_team", sa.Column("expertise", sa.JSON(), nullable=True))
    op.add_column("intake_team", sa.Column("business_unit", sa.String(120), nullable=True))
    op.execute("""UPDATE intake_team SET expertise = '["NDA","Vendor","Policy/FAQ"]' WHERE lower(key) = 'tier1'""")
    op.execute("""UPDATE intake_team SET expertise = '["Litigation","Privacy","Trademark","Contract Review","General"]' WHERE lower(key) = 'tier2'""")


def downgrade() -> None:
    op.drop_column("intake_team", "business_unit")
    op.drop_column("intake_team", "expertise")
