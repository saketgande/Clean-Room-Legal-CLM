"""Merge the Trademark Suite migration line into the main line."""

revision = "0073_merge_trademark_suite"
down_revision = ("0072_notice_deadline_basis", "0049_trademark_menu_labels")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
