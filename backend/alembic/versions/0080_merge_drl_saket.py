"""Merge the drl and drl-saket-v2 migration lines."""

revision = "0080_merge_drl_saket"
down_revision = ("0079_drop_nda_check", "0076_post_filing_tracking")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
